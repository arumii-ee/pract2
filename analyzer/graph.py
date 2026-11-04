"""호출 그래프 구성: 호출 대상 해석, 호출되는 곳(역방향), 콜백 등록, 전역 변수 사용, 실행 흐름 트리."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .parsers.c_parser import Call, Function, Loop, ParsedFile

STD_FUNCS = set("""
printf sprintf snprintf fprintf vprintf vsprintf vsnprintf vfprintf puts fputs putchar putc fputc
getchar getc fgetc gets fgets ungetc scanf sscanf fscanf vscanf vsscanf vfscanf perror
fopen freopen fclose fflush fread fwrite fseek ftell rewind fgetpos fsetpos feof ferror clearerr
remove rename tmpfile tmpnam setbuf setvbuf
memset memcpy memmove memcmp memchr strlen strcpy strncpy strcmp strncmp strcat strncat strchr
strrchr strstr strtok strspn strcspn strpbrk strerror strcoll strxfrm
malloc calloc realloc free aligned_alloc exit abort atexit quick_exit _Exit getenv system
atoi atol atoll atof strtol strtoul strtoll strtoull strtod strtof strtold abs labs llabs div ldiv
rand srand qsort bsearch assert
isalnum isalpha isblank iscntrl isdigit isgraph islower isprint ispunct isspace isupper isxdigit
tolower toupper
time clock difftime mktime localtime gmtime asctime ctime strftime
sin cos tan asin acos atan atan2 sinh cosh tanh exp log log10 log2 pow sqrt cbrt ceil floor
fabs fmod round lround trunc hypot fmin fmax sinf cosf tanf atan2f sqrtf powf fabsf floorf ceilf
roundf logf expf log10f isnan isinf
signal raise setjmp longjmp va_start va_end va_arg va_copy offsetof
""".split())

# POSIX / Linux 시스템 호출·라이브러리 (unistd, fcntl, pthread, socket, dirent, mman, stat, wait …)
POSIX_FUNCS = set("""
open close read write lseek pread pwrite fsync fdatasync ftruncate truncate dup dup2 pipe
unlink link symlink readlink access chdir getcwd rmdir mkdir chmod fchmod chown stat fstat lstat
utime utimes sync fcntl ioctl poll select pselect isatty
sleep usleep nanosleep alarm pause getpid getppid fork vfork execl execlp execle execv execvp
execve waitpid wait kill _exit setsid getuid geteuid sysconf
opendir readdir closedir rewinddir scandir sendfile
mmap munmap msync mprotect shm_open shm_unlink
popen pclose fdopen fileno strdup strndup strcasecmp strncasecmp strtok_r strerror_r
localtime_r gmtime_r clock_gettime clock_settime gettimeofday settimeofday timegm
sigaction sigemptyset sigfillset sigaddset sigprocmask
pthread_create pthread_join pthread_detach pthread_exit pthread_self pthread_cancel
pthread_mutex_init pthread_mutex_destroy pthread_mutex_lock pthread_mutex_trylock pthread_mutex_unlock
pthread_cond_init pthread_cond_destroy pthread_cond_wait pthread_cond_timedwait pthread_cond_signal
pthread_cond_broadcast pthread_attr_init pthread_attr_destroy pthread_attr_setdetachstate
pthread_attr_setstacksize pthread_attr_setschedpolicy pthread_attr_setschedparam
pthread_setschedparam pthread_setname_np pthread_rwlock_rdlock pthread_rwlock_wrlock pthread_rwlock_unlock
sem_init sem_destroy sem_wait sem_trywait sem_timedwait sem_post sem_open sem_close
socket bind listen accept connect send recv sendto recvfrom setsockopt getsockopt shutdown
getaddrinfo freeaddrinfo gai_strerror getnameinfo gethostbyname getifaddrs freeifaddrs
inet_addr inet_aton inet_ntoa inet_ntop inet_pton htons htonl ntohs ntohl
if_nametoindex epoll_create epoll_create1 epoll_ctl epoll_wait eventfd timerfd_create timerfd_settime
S_ISREG S_ISDIR S_ISLNK WIFEXITED WEXITSTATUS WIFSIGNALED WTERMSIG
FD_ZERO FD_SET FD_CLR FD_ISSET
""".split())

ENTRY_NAMES = ("main", "wmain", "WinMain", "wWinMain")


@dataclass
class Target:
    kind: str                       # user / macro / lib / libmacro / proto / std / posix / unknown / indirect
    name: str
    func: Function | None = None
    loc: str = ""
    doc: str = ""


class CodeIndex:
    def __init__(self, user: list[ParsedFile], lib: list[ParsedFile], hls_tops: set[str] | None = None):
        self.user_files = user
        self.lib_files = lib
        self.hls_tops = hls_tops or set()
        self.funcs: list[Function] = [f for pf in user for f in pf.functions]
        self.by_name: dict[str, list[Function]] = defaultdict(list)
        for f in self.funcs:
            self.by_name[f.name].append(f)

        self.user_macros = {n: (pf.path, m) for pf in user for n, m in pf.macros.items()}
        self.user_protos = {n: (pf.path, v) for pf in user for n, v in pf.prototypes.items()}
        self.lib_funcs: dict[str, Function] = {}
        self.lib_macros: dict = {}
        self.lib_protos: dict = {}
        for pf in lib:
            for f in pf.functions:
                self.lib_funcs.setdefault(f.name, f)
            for n, m in pf.macros.items():
                self.lib_macros.setdefault(n, (pf.path, m))
            for n, v in pf.prototypes.items():
                self.lib_protos.setdefault(n, (pf.path, v))

        self.globals: dict[str, list] = defaultdict(list)
        for pf in user:
            for g in pf.globals:
                self.globals[g.name].append((pf.path, g))

        self.callers: dict[Function, list[tuple[Function, Call]]] = defaultdict(list)
        self.registered: dict[Function, list[tuple[Function, object]]] = defaultdict(list)
        self.global_users: dict[str, list[Function]] = defaultdict(list)
        self._link()

    # ── 해석 ──
    def resolve(self, name: str, fn: Function) -> Target:
        if name.startswith("->"):
            return Target("indirect", name)
        cands = self.by_name.get(name)
        if cands:
            same = [c for c in cands if c.path == fn.path]
            public = [c for c in cands if not c.is_static]
            f = (same or public or cands)[0]
            return Target("user", name, f, f"{f.path.as_posix()}:{f.line}")
        if name in self.user_macros:
            p, m = self.user_macros[name]
            return Target("macro", name, loc=f"{p.as_posix()}:{m.line}", doc=m.value)
        if name in self.lib_funcs:
            f = self.lib_funcs[name]
            return Target("lib", name, f, f"{f.path.as_posix()}:{f.line}", f.doc[0] if f.doc else "")
        if name in self.lib_macros:
            p, m = self.lib_macros[name]
            return Target("libmacro", name, loc=f"{p.as_posix()}:{m.line}", doc=m.value)
        if name in self.user_protos:
            p, (ln, _) = self.user_protos[name]
            return Target("proto", name, loc=f"{p.as_posix()}:{ln}")
        if name in self.lib_protos:
            p, (ln, _) = self.lib_protos[name]
            return Target("lib", name, loc=f"{p.as_posix()}:{ln}")
        if name in STD_FUNCS:
            return Target("std", name)
        if name in POSIX_FUNCS:
            return Target("posix", name)
        return Target("unknown", name)

    def _link(self):
        func_names = set(self.by_name) | set(self.lib_funcs)
        for fn in self.funcs:
            for c in fn.calls:
                c.target = self.resolve(c.name, fn)
                if c.target.kind == "user":
                    self.callers[c.target.func].append((fn, c))
            seen_g = set()
            for r in fn.refs:
                if r.name in func_names and r.name not in self.globals:
                    t = self.resolve(r.name, fn)
                    fn.callbacks.append((r, t))
                    if t.kind == "user":
                        self.registered[t.func].append((fn, r))
                elif r.name in self.globals and r.name not in seen_g:
                    seen_g.add(r.name)
                    fn.globals_used.append(r.name)
                    self.global_users[r.name].append(fn)

    # ── 진입점 ──
    def entry_functions(self) -> list[Function]:
        out = [f for n in ENTRY_NAMES for f in self.by_name.get(n, [])]
        out += [f for n in sorted(self.hls_tops) for f in self.by_name.get(n, []) if f not in out]
        out += [f for f in self.funcs if f.name.startswith("luaopen_") and f not in out]
        return out

    def async_functions(self) -> list[Function]:
        return [f for f in self.funcs if f in self.registered]

    def unreached(self) -> list[Function]:
        roots = self.entry_functions() + self.async_functions()
        seen, stack = set(roots), list(roots)
        while stack:
            f = stack.pop()
            nxt = [c.target.func for c in f.calls if c.target and c.target.kind == "user"]
            nxt += [t.func for _, t in f.callbacks if t.kind == "user"]
            for g in nxt:
                if g not in seen:
                    seen.add(g)
                    stack.append(g)
        return [f for f in self.funcs if f not in seen]

    def role(self, fn: Function) -> list[str]:
        tags = []
        if fn.name in ENTRY_NAMES:
            tags.append("프로그램 진입점")
        if fn.name in self.hls_tops:
            tags.append("HLS top 함수")
        if fn.name.startswith("luaopen_"):
            tags.append("Lua C 모듈 진입점")
        if fn in self.registered:
            tags.append("콜백/인터럽트 핸들러")
        if fn.is_static:
            tags.append("static (파일 내부 전용)")
        return tags


# ───────────────────────── 실행 흐름 트리 ─────────────────────────

KIND_TAG = {
    "lib": "BSP/라이브러리", "libmacro": "BSP 매크로", "macro": "매크로",
    "std": "표준 C", "posix": "POSIX/Linux", "proto": "선언만 있음", "unknown": "외부/미확인",
    "indirect": "함수 포인터 호출",
}


def _body_tree(fn: Function):
    loops: list[Loop] = sorted(fn.loops + fn.branches, key=lambda b: (b.start, -b.end))
    parent: dict[int, int | None] = {}
    for i, lp in enumerate(loops):
        parent[i] = next((j for j in range(i - 1, -1, -1)
                          if loops[j].start <= lp.start and lp.end <= loops[j].end), None)
    children: dict[int | None, list] = defaultdict(list)
    for i, lp in enumerate(loops):
        children[parent[i]].append((lp.start, "loop", i))
    for c in fn.calls:
        inner = None
        for i, lp in enumerate(loops):
            if lp.start <= c.pos < lp.end:
                inner = i
        children[inner].append((c.pos, "call", c))

    def build(key):
        out = []
        for _, kind, obj in sorted(children[key], key=lambda x: x[0]):
            if kind == "loop":
                sub = build(obj)
                if loops[obj].kind in BRANCH_KINDS and not sub:
                    continue                 # 호출이 없는 분기는 생략
                out.append(("loop", loops[obj], sub))
            else:
                out.append(("call", obj))
        return out
    return build(None)


BRANCH_KINDS = {"if", "elif", "else"}


def loop_label(lp: Loop) -> str:
    if lp.kind == "if":
        return f"❓ if ({lp.cond}) 일 때"
    if lp.kind == "elif":
        return f"❓ else if ({lp.cond}) 일 때"
    if lp.kind == "else":
        return "❓ else 일 때"
    if lp.kind == "wait":
        return f"⏳ while ({lp.cond}); — 조건이 바뀔 때까지 대기(폴링)"
    if lp.kind == "do":
        return f"🔁 do … while ({lp.cond})" + (" — 무한 반복" if lp.infinite else "")
    return f"🔁 {lp.kind} ({lp.cond})" + (" — 무한 반복" if lp.infinite else " 반복")


def flow_tree(index: CodeIndex, root: Function, max_depth: int = 8, max_lines: int = 400) -> list[str]:
    lines = [f"{root.name}()    [{root.path.as_posix()}:{root.line}]"]
    expanded: set[Function] = {root}

    def call_label(fn: Function, c: Call, count: int) -> str:
        t: Target = c.target
        s = f"{c.name}({c.args})" + (f" ×{count}" if count > 1 else "")
        if t.kind == "user":
            s += f"    → {t.loc}"
        else:
            where = f": {Path(t.loc.split(':')[0]).name}" if t.loc and t.kind in ("lib", "libmacro") else ""
            s += f"    [{KIND_TAG.get(t.kind, t.kind)}{where}]"
        regs = [r.name for r, _ in fn.callbacks if r.via_pos == c.pos]
        if regs:
            s += "    ⚡ " + ", ".join(regs) + " 등록"
        return s

    def render(fn: Function, items, prefix: str, depth: int, stack: frozenset):
        # 연속으로 같은 함수를 호출하면 하나로 합친다
        merged = []
        for it in items:
            if it[0] == "call" and merged and merged[-1][0] == "call" and merged[-1][1].name == it[1].name:
                merged[-1] = ("call", merged[-1][1], merged[-1][2] + 1)
            else:
                merged.append(it if it[0] == "loop" else ("call", it[1], 1))
        for i, it in enumerate(merged):
            if len(lines) >= max_lines:
                lines.append(prefix + "… (출력 줄 수 제한으로 생략)")
                return
            last = i == len(merged) - 1
            conn, child = ("└─ ", "   ") if last else ("├─ ", "│  ")
            if it[0] == "loop":
                lines.append(prefix + conn + loop_label(it[1]))
                render(fn, it[2], prefix + child, depth, stack)
                continue
            c, count = it[1], it[2]
            label = call_label(fn, c, count)
            sub = c.target.func if c.target and c.target.kind == "user" else None
            note, expand = "", False
            if sub is not None:
                if sub in stack:
                    note = "    ↺ 재귀 호출"
                elif depth >= max_depth:
                    note = "    … (깊이 제한)"
                elif sub in expanded:
                    note = "    ↑ 위에서 이미 펼침" if sub.calls else ""
                else:
                    expand = True
            lines.append(prefix + conn + label + note)
            if expand:
                expanded.add(sub)
                render(sub, _body_tree(sub), prefix + child, depth + 1, stack | {sub})

    render(root, _body_tree(root), "", 1, frozenset({root}))
    return lines


# ───────────────────────── 요약용: 핵심 흐름 / 스레드 구성 / 파일 간 호출 ─────────────────────────

def _registered_at(fn: Function, c: Call) -> list[Function]:
    """이 호출의 인자로 등록되는 사용자 함수들 (pthread_create(…, worker, …) → [worker])"""
    return [t.func for r, t in fn.callbacks if r.via_pos == c.pos and t.kind == "user"]


def _skel_items(fn: Function):
    """_body_tree에서 사용자 함수 호출·콜백 등록·그것을 품은 반복문만 남긴다. 조건 분기는 펼쳐서 없앤다."""
    def walk(items):
        out = []
        for it in items:
            if it[0] == "loop":
                sub = walk(it[2])
                if not sub:
                    continue
                if it[1].kind in BRANCH_KINDS:
                    out += sub
                else:
                    out.append(("loop", it[1], sub))
                continue
            c = it[1]
            regs = _registered_at(fn, c)
            if c.target is not None and c.target.kind == "user":
                out.append(("call", c.target.func, c))
            if regs:
                out.append(("reg", regs, c))
        return out
    return walk(_body_tree(fn))


def short_loop(lp: Loop) -> str:
    if lp.infinite:
        return "🔁 무한 반복"
    if lp.kind == "wait":
        return "⏳ 대기"
    cond = lp.cond if len(lp.cond) <= 40 else lp.cond[:39] + "…"
    return f"🔁 반복 ({cond})"


def skeleton(index: CodeIndex, root: Function, max_depth: int = 3, max_lines: int = 80,
             describe=None) -> list[str]:
    """사용자 함수 호출만 남긴 '핵심 흐름' 트리. 최상위 단계에는 순서 번호를 붙인다."""
    describe = describe or (lambda f: "")
    lines = [f"{root.name}()" + (f"  — {describe(root)}" if describe(root) else "")]
    expanded: set[Function] = {root}

    def merge(items):
        merged = []
        for it in items:
            if (it[0] == "call" and merged and merged[-1][0] == "call" and merged[-1][1] is it[1]):
                merged[-1] = (*merged[-1][:3], merged[-1][3] + 1)
            else:
                merged.append((*it, 1) if it[0] == "call" else it)
        return merged

    def render(items, prefix: str, depth: int, number: bool, top: bool):
        """top=True: 진입점 바로 아래 단계(반복문 안 포함). 줄 수가 넘쳐도 순서가 끊기지 않게 항상 다 쓴다."""
        merged = merge(items)
        step = 0
        for i, it in enumerate(merged):
            if len(lines) >= max_lines and not top:
                lines.append(prefix + "└─ …")
                return
            last = i == len(merged) - 1
            conn, child = ("└─ ", "   ") if last else ("├─ ", "│  ")
            if number:
                step += 1
            num = f"{step}. " if number else ""
            if it[0] == "loop":
                lines.append(prefix + conn + num + short_loop(it[1]))
                render(it[2], prefix + child, depth, False, top)
            elif it[0] == "reg":
                names = ", ".join(f"{f.name}()" for f in it[1])
                lines.append(prefix + conn + num + f"⚡ {names} 시작/등록  ({it[2].name})")
            else:
                sub, count = it[1], it[3]
                desc = describe(sub)
                label = f"{sub.name}()" + (f" ×{count}" if count > 1 else "") + (f"  — {desc}" if desc else "")
                expand = (sub not in expanded and depth < max_depth and sub.calls
                          and len(lines) < max_lines)
                if sub in expanded and _skel_items(sub):
                    label += "  ↑"
                lines.append(prefix + conn + num + label)
                if expand:
                    expanded.add(sub)
                    render(_skel_items(sub), prefix + child, depth + 1, False, False)

    render(_skel_items(root), "", 1, True, True)
    if len(lines) == 1:
        lines.append("└─ (사용자 함수 호출 없음)")
    return lines


def registrations_in_order(index: CodeIndex, root: Function) -> list[tuple[Function, object, Function]]:
    """root에서 직접 호출로 따라가며 만나는 콜백/스레드 등록을 소스 순서대로. [(등록한 함수, IdentRef, 대상)]"""
    out, seen = [], {root}

    def walk(fn: Function):
        events = [(c.pos, "call", c) for c in fn.calls] + [(r.pos, "reg", (r, t)) for r, t in fn.callbacks]
        for _, kind, obj in sorted(events, key=lambda e: e[0]):
            if kind == "reg":
                r, t = obj
                if t.kind == "user":
                    out.append((fn, r, t.func))
            elif obj.target is not None and obj.target.kind == "user" and obj.target.func not in seen:
                seen.add(obj.target.func)
                walk(obj.target.func)
    walk(root)
    return out


def thread_map(index: CodeIndex) -> list[tuple[Function, Function, Function, object]]:
    """진입점부터 '누가 어떤 스레드/콜백을 어떤 순서로 시작하는지'. [(소유 진입점, 등록한 함수, 대상, IdentRef)]
    등록된 함수도 다시 소유자가 되어(스레드 안에서 또 스레드를 만드는 경우) 차례로 따라간다."""
    edges, owned = [], set()
    queue = list(index.entry_functions())
    done: set[Function] = set()
    while queue:
        root = queue.pop(0)
        if root in done:
            continue
        done.add(root)
        for caller, r, tgt in registrations_in_order(index, root):
            if tgt in owned or tgt is root:
                continue
            owned.add(tgt)
            edges.append((root, caller, tgt, r))
            queue.append(tgt)
    return edges


def file_call_edges(index: CodeIndex) -> dict[tuple[Path, Path], int]:
    """파일 간 사용자 함수 호출 수. 같은 파일 안의 호출은 제외."""
    out: dict[tuple[Path, Path], int] = defaultdict(int)
    for fn in index.funcs:
        for c in fn.calls:
            if c.target is not None and c.target.kind == "user" and c.target.func.path != fn.path:
                out[(fn.path, c.target.func.path)] += 1
        for _, t in fn.callbacks:
            if t.kind == "user" and t.func.path != fn.path:
                out[(fn.path, t.func.path)] += 1
    return dict(out)
