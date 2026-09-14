"""UI 端：扫描会话状态文件、清理僵尸、聚合成一盏灯。"""

import json
import time

import core


class Session:
    """一个 Claude Code 会话的当前快照。"""

    __slots__ = ("session_id", "cwd", "project", "state", "reason",
                 "title", "tool", "updated_at", "owner_pid", "owner_name",
                 "chain", "path")

    def __init__(self, data, path):
        self.session_id = data.get("session_id") or path.stem
        self.cwd = data.get("cwd") or ""
        self.project = data.get("project") or core.project_name(self.cwd)
        self.state = data.get("state") or core.DARK
        self.reason = data.get("reason") or ""
        #: 该会话的第一条用户指令摘要。本次升级前开的会话没有这个字段，
        #: 会是空串 —— 显示层要自己回退（见 tooltip.session_name）。
        self.title = data.get("title") or ""
        self.tool = data.get("tool") or ""
        self.updated_at = float(data.get("updated_at") or 0)
        self.owner_pid = data.get("owner_pid")
        self.owner_name = (data.get("owner_name") or "")
        self.chain = data.get("chain") or []
        self.path = path

    @property
    def age(self):
        return max(0.0, time.time() - self.updated_at)


class Aggregator:
    """扫描 + 僵尸清理 + 优先级聚合。

    僵尸判定分两条路，因为它们的可信度不同：

    * **认得出主人**（owner_name 是 claude/node/bun）—— 直接信 PID 存活。
      这是主路径，能立刻捕捉到 Claude 进程崩溃，也能区分"会话死了"和
      "会话只是长时间没动静"。
    * **认不出主人**（退回成了 VSCode 主进程）—— 只靠时间兜底。
      VSCode 还开着不代表会话还活着，所以这条不可信，只能防长期堆积。

    刻意**不用**"多久没心跳"当作主判据：Claude 思考几分钟不发任何事件是正常的，
    拿沉默当死亡会把正在干活的会话误杀。
    """

    STALE_SECONDS = 6 * 3600  # 认不出主人时的兜底清理阈值

    def __init__(self, sessions_dir=None):
        self.dir = sessions_dir or core.SESSIONS_DIR

    def scan(self):
        """返回当前所有活着的会话；顺手删掉僵尸的状态文件。"""
        sessions = []
        try:
            files = list(self.dir.glob("*.json"))
        except OSError:
            return sessions

        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue  # 刚被 SessionEnd 删掉，下轮自然就没了
            except OSError:
                # 读失败几乎总是撞上了 Windows 的文件占用（hook 正在原子替换）。
                # **绝不能因此删掉会话** —— 那等于凭空抹掉一次告警。
                # 跳过这一轮，下次扫描再读。
                continue
            except Exception:
                # 真的解析不了。可能是历史遗留的坏文件，只清很旧的。
                self._drop_if_old(path)
                continue

            if not isinstance(data, dict):
                self._drop_if_old(path)
                continue

            session = Session(data, path)
            if self._is_zombie(session):
                self._drop(path)
                continue
            sessions.append(session)

        sessions.sort(key=lambda s: (core.PRIORITY.get(s.state, 9), -s.updated_at))
        return sessions

    def _is_zombie(self, session):
        name = session.owner_name.lower()
        if session.owner_pid and name in core.OWNER_CANDIDATES:
            return not core.pid_alive(session.owner_pid)
        return session.age > self.STALE_SECONDS

    @staticmethod
    def _drop(path):
        core.debug(f"DROP(zombie) {path.name}", exc_info=True)
        try:
            path.unlink()
        except OSError:
            pass

    def _drop_if_old(self, path):
        """只清真正老旧的坏文件，避免把暂时读不到的文件误判成垃圾。"""
        try:
            age = time.time() - path.stat().st_mtime
            if age > self.STALE_SECONDS:
                core.debug(f"DROP(old {age:.0f}s) {path.name}", exc_info=True)
                path.unlink()
        except OSError:
            pass

    def current(self):
        """返回 (当前灯态, 会话列表)。"""
        sessions = self.scan()
        if not sessions:
            return core.DARK, []
        return sessions[0].state, sessions

    # ------------------------------------------------------------ 交互

    @staticmethod
    def acknowledge(session, by="unknown"):
        """把绿态"点掉"，转为暗。下个回合自然会被重新点亮。

        `by` 会写进状态文件（acked_by），用来区分"人点的"和"别的东西写的"——
        排查"绿灯莫名消失"这类问题时，这一条就是决定性证据。
        """
        core.debug(f"ACKNOWLEDGE {session.session_id} by={by}", exc_info=True)
        try:
            data = json.loads(session.path.read_text(encoding="utf-8"))
            data["state"] = core.DARK
            data["reason"] = ""
            data["acked_at"] = time.time()
            data["acked_by"] = by
            session.path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    @staticmethod
    def ignore(session, by="unknown"):
        """人肉"忽略"：删掉这一个会话的状态文件，把它从聚合里摘出去。

        与 acknowledge 的区别：ack 是**改状态**（会话还在，下个回合照常亮）；
        ignore 是**清文件**（会话从聚合里彻底消失）。

        之所以需要这个出口：红态唯一的自动出口是"同一个会话的下一个事件"，
        而被丢下的会话（VSCode 窗口开着、claude.exe 还活着）永远不会有下一个
        事件——僵尸清理也够不着它，因为 `_is_zombie` 对认得出的主人只信 PID
        存活，那个 6 小时的兜底根本走不到。结果是红灯永远闪，还因为红的绝对
        优先级把别的会话的绿/黄一起盖住。

        删除是**可逆的**：hook_writer 对改变灯态的事件会无条件重写文件，所以
        这个会话下次真有动静时会自己回来（见 hook_writer.handle 里那个
        `if not path.exists(): return`——不改变灯态的事件不会让它复活）。

        刻意用 unlink，而不是像 acknowledge 那样读改写：不持有文件句柄，
        就不会和 hook 的 os.replace 抢文件锁。删除幂等，文件已不在不算失败。
        """
        core.debug(f"IGNORE {session.session_id} by={by} file={session.path.name}")
        try:
            session.path.unlink()
            return True
        except FileNotFoundError:
            return False  # 已被 SessionEnd 或上一轮扫描处理掉了——目标已达成
        except OSError:
            # Windows 上撞见 hook 正在原子替换时的文件占用。返回 False，
            # 下一轮 250ms 轮询里这个会话还在，用户会看到灯没灭，再点一次即可。
            core.debug(f"IGNORE-FAIL {session.session_id} by={by}")
            return False
