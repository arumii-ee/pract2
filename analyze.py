#!/usr/bin/env python3
"""사용법: python analyze.py <프로젝트 경로> [-d 깊이] [-e 제외목록] [-o 출력파일]"""

import sys

from analyzer.cli import main

if __name__ == "__main__":
    sys.exit(main())
