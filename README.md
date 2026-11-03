# project-analyzer

프로젝트 폴더를 분석해 코드 구조를 정리하는 스크립트입니다. **C / Lua / Python**에 맞춰져 있고,
**Xilinx/AMD Vitis Classic 워크스페이스**(2019.2~2023.1, 2020.2 포함)를 인식합니다. Python 3.9+ 표준 라이브러리만 사용합니다.

## 사용법

```bash
python analyze.py <경로>                          # 터미널 요약
python analyze.py <경로> -f md -o STRUCTURE.md    # 상세 리포트 (구조·함수 상세·실행 흐름)
```

| 옵션 | 설명 |
|---|---|
| `-f text\|md` | 출력 형식 (기본 text) |
| `-o 파일` | 결과 저장 |
| `-d N` | 디렉터리 트리 깊이 제한 |
| `-e a,b` | 추가로 제외할 폴더/파일 이름 |
| `--flow-depth N` | 실행 흐름 트리 깊이 (기본 8) |
| `--no-source` | md 리포트에 함수 소스 코드 생략 |
| `--include-generated` | Vitis BSP/FSBL 자동 생성 코드도 사용자 코드처럼 분석 |

Vitis는 **워크스페이스 폴더 전체**를 넘기는 것을 권장합니다. 그래야 BSP 함수/`xparameters.h`까지 찾아서 호출 대상을 해석합니다.

## Markdown 리포트 구성 (`-f md`)

1. **개요** — 프로젝트 형태, 플랫폼(CPU·도메인·XSA), 앱/시스템/HLS 프로젝트, BSP 드라이버 목록, 언어 통계,
   메모리 맵(`lscript.ld`), 하드웨어 주변장치(`xparameters.h`), 코드에서 쓰는 `XPAR_*` 매크로
2. **전체 구조** — 디렉터리 트리(자동 생성 폴더는 접음), 파일별 역할, include 관계도, 함수 호출 관계도(Mermaid)
3. **실행 흐름** — `main()`부터 소스에 적힌 호출 순서대로 펼친 트리
   - `🔁` 반복문(무한 루프 표시) · `⏳` 폴링 대기 `while(!flag);` · `❓` 조건 분기 · `⚡` 콜백/ISR 등록
   - 인터럽트 핸들러·콜백(`XScuGic_Connect`, `XTmrCtr_SetHandler`, `xTaskCreate` 등으로 등록된 함수)은 비동기 진입점으로 따로 흐름 표시
   - HLS top 함수(`hls.app`의 `top`, `set_top`)도 진입점으로 표시
   - 진입점에서 도달하지 않는 함수 목록
4. **함수 상세** — 시그니처, 위치, 설명 주석, 호출하는 함수(순서), 호출되는 곳, 등록하는 콜백, 사용하는 전역 변수, 반복문, HLS pragma, 소스
5. **자료형·매크로·전역 변수** — 전역 변수는 "어떤 함수가 사용하는지"까지 표시

## Vitis 처리 방식

| 대상 | 처리 |
|---|---|
| `.metadata`, `RemoteSystemsTempFiles`, `Debug/`, `Release/`, `.Xil` | 탐색 제외 |
| 플랫폼 프로젝트(`platform.spr` 폴더), `libsrc`, `bsp`, `zynq_fsbl`, `export`, `_ide`, HLS `solution*/syn·impl·sim` | **자동 생성 코드**: 통계·상세 분석 제외, 함수/매크로 정의만 색인해 호출 대상 해석에 사용 |
| `<app>/src/` 등 나머지 | 사용자 코드: 전체 분석 |
| `.xsa`, `.bit`, `.elf` | 바이너리 → 건너뜀 (이름만 플랫폼 정보에 표시) |

## 구조

```
analyze.py                   실행 진입점
analyzer/
├── cli.py                   인자 파싱 및 파이프라인 조립
├── models.py                ProjectReport 등 결과 데이터 모델
├── scanner.py               파일 탐색 / 제외 규칙
├── languages.py             언어 판별, 줄 수 세기
├── detectors.py             빌드 시스템·라이브러리·진입점 감지
├── vitis.py                 Vitis 워크스페이스 분석 (플랫폼/앱/HLS/lscript/xparameters)
├── parsers/c_parser.py      C/C++ 경량 파서 (함수·호출·반복/분기·콜백·전역·매크로·자료형)
├── graph.py                 호출 그래프, 콜백 등록 추적, 실행 흐름 트리
└── reporters/
    ├── text.py              터미널 요약
    └── markdown.py          상세 Markdown 리포트
tests/                       단위 테스트 + Vitis 2020.2 형태 샘플 워크스페이스
```

## 테스트

```bash
python -m unittest discover -s tests -t .
```

## 한계

컴파일러 없이 정적으로 분석합니다.
- `#ifdef` 분기는 양쪽 모두 읽고, 매크로 전개는 하지 않습니다.
- 함수 포인터 배열/구조체 멤버를 통한 호출은 `[함수 포인터 호출]`로만 표시됩니다.
- 실행 흐름은 소스 순서 기준이며 실제 런타임 분기 결과는 반영하지 않습니다.
- Lua/Python은 아직 통계·진입점만 지원합니다. 함수 분석은 다음 단계입니다.
