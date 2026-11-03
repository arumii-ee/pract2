# project-analyzer

프로젝트 폴더를 분석해 코드 구조를 요약하는 스크립트입니다. **C / Lua / Python** 프로젝트에 맞춰져 있고, Python 3.9+ 표준 라이브러리만 사용합니다.

## 사용법

```bash
python analyze.py <프로젝트 경로>              # 전체 출력
python analyze.py <경로> -d 2                  # 트리를 깊이 2까지만
python analyze.py <경로> -e "third_party,vendor"  # 추가 제외
python analyze.py <경로> -o STRUCTURE.txt      # 파일로 저장
```

## 출력 내용

| 섹션 | 내용 |
|---|---|
| 빌드 시스템 | Make, CMake, Meson, Autotools, SCons, Premake, xmake, Visual Studio, LuaRocks, pyproject 등 |
| 라이브러리/프레임워크 | C `#include`(Lua C API, SDL, raylib, GLFW…), Lua `require`(LuaSocket, LFS, LPeg…), LÖVE, Python `import` |
| 진입점 후보 | C `main()`/`WinMain()`, `luaopen_*()` Lua C 모듈, `main.lua`/`init.lua`, shebang 스크립트, `if __name__ == "__main__"` |
| 언어별 통계 | 파일 수, 코드/주석/빈 줄 (C `//` `/* */`, Lua `--` `--[==[ ]==]`, Python `#` docstring 구분) |
| 디렉터리 트리 | 폴더별 파일 수, 파일별 줄 수 |

## 기본 제외 대상

`.git`, `node_modules`, `__pycache__`, `venv`, `build`, `dist`, `bin`, `obj`, `out`, `Debug`, `Release`, `CMakeFiles` 폴더,
바이너리 파일, 1MB 초과 파일, 심볼릭 링크, 루트 `.gitignore` 패턴(간이 지원).

## 구조

```
analyze.py              실행 진입점
analyzer/
├── cli.py              인자 파싱 및 파이프라인 조립
├── models.py           ProjectReport 등 결과 데이터 모델
├── scanner.py          파일 탐색 / 제외 규칙
├── languages.py        언어 판별, 줄 수 세기
├── detectors.py        빌드 시스템·라이브러리·진입점 감지
└── reporters/text.py   텍스트 리포트 출력
```

## 다음 단계 (예정)

3. 파서 (C 함수/구조체, Lua 함수/모듈, Python AST) + include/require 의존성 그래프
4. Markdown / JSON 출력
