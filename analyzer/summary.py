"""함수 한 줄 요약 (규칙 기반).

1) 함수 위 주석의 첫 문장  2) 함수 이름의 동사 + 대상 단어  3) 호출·구조로 본 성격 태그
순서로 "무슨 함수인지"를 짧게 만든다. 외부 서비스 없이 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import re

from .parsers.c_parser import Function, is_prose

MAX_LEN = 60

# 이름에 나오는 동사 → 한국어 (앞쪽에 있을수록 우선)
VERBS = {
    "init": "초기화", "initialize": "초기화", "initial": "초기화", "setup": "설정", "config": "설정",
    "configure": "설정", "reset": "초기화", "clear": "지우기", "deinit": "해제", "close": "닫기",
    "open": "열기", "start": "시작", "stop": "중지", "run": "실행", "main": "프로그램 진입점",
    "send": "전송", "tx": "송신", "transmit": "전송", "write": "쓰기", "put": "쓰기", "push": "넣기",
    "publish": "발행", "subscribe": "구독",
    "recv": "수신", "receive": "수신", "rx": "수신", "read": "읽기", "pop": "꺼내기", "fetch": "가져오기",
    "load": "불러오기", "save": "저장", "store": "저장", "record": "기록", "log": "기록",
    "get": "조회", "set": "설정", "update": "갱신", "sync": "동기화", "refresh": "갱신",
    "make": "생성", "create": "생성", "build": "생성", "gen": "생성", "generate": "생성", "add": "추가",
    "alloc": "할당", "free": "해제", "release": "해제", "delete": "삭제", "remove": "삭제",
    "check": "확인", "is": "여부 확인", "has": "여부 확인", "verify": "검증", "validate": "검증",
    "test": "테스트", "process": "처리", "proc": "처리", "handle": "처리", "handler": "처리",
    "parse": "파싱", "decode": "디코딩", "encode": "인코딩", "convert": "변환", "to": "변환",
    "calc": "계산", "cal": "계산", "calculate": "계산", "compute": "계산", "count": "개수 계산",
    "show": "표시", "display": "표시", "draw": "그리기", "print": "출력", "list": "목록",
    "find": "찾기", "search": "검색", "copy": "복사", "memcpy": "복사", "map": "매핑", "remap": "재매핑",
    "compress": "압축", "decompress": "압축 해제", "inflate": "압축 해제", "deflate": "압축",
    "wait": "대기", "lock": "잠금", "unlock": "잠금 해제", "enable": "활성화", "disable": "비활성화",
    "register": "등록", "connect": "연결", "accept": "연결 수락", "bind": "바인드",
    "thread": "스레드", "task": "태스크", "isr": "인터럽트 처리", "interrupt": "인터럽트 처리",
    "callback": "콜백", "cb": "콜백", "func": "함수",
}
# 동사가 없을 때 이름 끝 단어로 성격을 붙인다
SUFFIX_ROLE = {"thread": "스레드", "task": "태스크", "handler": "처리", "isr": "인터럽트 처리",
               "callback": "콜백", "cb": "콜백", "func": "함수", "interrupt": "인터럽트 처리"}

# 호출되는 외부 함수 → 성격 태그
CALL_TAGS = [
    ("네트워크", {"socket", "bind", "listen", "accept", "connect", "send", "recv", "sendto", "recvfrom",
                "setsockopt", "getifaddrs", "inet_ntop", "inet_pton"}),
    ("파일 I/O", {"fopen", "fclose", "fwrite", "fread", "fprintf", "fgets", "fputs", "open", "opendir",
                 "readdir", "unlink", "rename", "mkdir", "stat", "statvfs"}),
    ("메모리 매핑", {"mmap", "munmap", "shm_open"}),
    ("동기화", {"pthread_mutex_lock", "pthread_cond_wait", "sem_wait", "sem_post", "pthread_cond_signal"}),
    ("스레드 생성", {"pthread_create"}),
    ("시리얼", {"sp_blocking_write", "sp_blocking_read", "sp_open", "sp_nonblocking_read"}),
    ("GPIO", {"gpiod_line_request_input", "gpiod_line_get_value", "gpiod_line_event_wait",
              "gpiod_chip_open_by_name", "gpiod_line_set_value"}),
]


def split_words(name: str) -> list[str]:
    """snake_case / CamelCase / 숫자 경계를 기준으로 이름을 단어로 나눈다."""
    parts = []
    for chunk in re.split(r'_+', name):
        parts += re.findall(r'[A-Z]+\d*(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+(?:\d+(?![A-Z]))?|[A-Z]+\d*|\d+', chunk)
    return [p for p in parts if p]


def _clip(s: str) -> str:
    s = re.sub(r'\s+', ' ', s).strip()
    return s if len(s) <= MAX_LEN else s[:MAX_LEN - 1] + "…"


def from_doc(fn: Function) -> str:
    for line in fn.doc:
        line = re.sub(r'^[@\\](brief|fn|func|function)\s+', '', line).strip()
        if line.startswith(("@", "\\")):            # @param, @return …
            continue
        if is_prose(line) or re.search(r'[가-힣]{2,}', line):
            return _clip(line)
    return ""


# 임베디드 코드에서 명사로 더 자주 쓰이는 단어: 다른 동사가 없을 때만 동사로 본다
WEAK_VERBS = {"register", "map", "list", "log", "record", "count", "test", "to", "rx", "tx", "func",
              "cb", "callback", "main", "is", "has", "free", "set"}


def _pick_verb(low: list[str]) -> int | None:
    """동사 위치: 맨 앞 동사(get_x, make_x) → 맨 뒤 동사(uart_buffer_write) → 약한 동사"""
    cands = [i for i, w in enumerate(low) if w in VERBS and not (w in SUFFIX_ROLE and i == len(low) - 1)]
    if not cands:
        return None
    strong = [i for i in cands if low[i] not in WEAK_VERBS]
    if low[0] in VERBS and (low[0] not in WEAK_VERBS or not strong) and 0 in cands:
        return 0
    if strong:
        return strong[-1]
    return cands[-1]


def from_name(name: str) -> str:
    words = split_words(name)
    low = [w.lower() for w in words]
    i = _pick_verb(low)
    role = SUFFIX_ROLE.get(low[-1]) if low else None
    if i is not None:
        rest = [words[j] for j in range(len(words)) if j != i]
        if role and i != len(low) - 1 and rest and rest[-1].lower() == low[-1]:
            rest = rest[:-1]
        else:
            role = None
        return _clip(" ".join(rest + [VERBS[low[i]]]) + (f" {role}" if role else ""))
    if role:
        return _clip(" ".join(words[:-1] + [role]))
    return _clip(" ".join(words))


def tags(fn: Function, index=None) -> list[str]:
    out = []
    if index is not None and fn in index.registered:
        via = {r.via for _, r in index.registered[fn]}
        out.append("⚡ 스레드" if "pthread_create" in via else "⚡ 콜백/핸들러")
    if any(lp.infinite for lp in fn.loops):
        out.append("🔁 상주 루프")
    names = {c.name for c in fn.calls}
    for tag, keys in CALL_TAGS:
        if names & keys:
            out.append(tag)
    user_calls = [c for c in fn.calls if c.target is not None and getattr(c.target, "kind", "") == "user"]
    if not user_calls and fn.length <= 15 and not out:
        out.append("유틸")
    return out[:3]


def summarize(fn: Function, index=None) -> tuple[str, str]:
    """(요약문, 출처) — 출처는 '주석' 또는 '추정'"""
    doc = from_doc(fn)
    if doc:
        text, src = doc, "주석"
    else:
        text, src = from_name(fn.name), "추정"
    t = [x for x in tags(fn, index) if not (x == "⚡ 스레드" and "스레드" in text)]
    if t:
        text += " · " + " · ".join(t)
    return text, src
