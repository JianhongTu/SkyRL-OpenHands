#!/usr/bin/env bash

source ~/.bashrc

/testbed/.venv/bin/python -m ensurepip --default-pip

ln -s /testbed/.venv /root/.venv
ln -s /testbed/.venv/bin/python /root/.local/bin/python
ln -s /testbed/.venv/bin/python /root/.local/bin/python3

find "/testbed/.venv/bin" -type f -executable -exec ln -sf {} "/root/.local/bin/" \;

export PATH=/root/.local/bin:$PATH
export PATH=/testbed/.venv/bin:$PATH

/testbed/.venv/bin/python -m pip install chardet networkx
/testbed/.venv/bin/python -m pip install 'rank-bm25>=0.2.0,<1.0.0'
echo "Custom BM25 components installed successfully"

find . -name '*.pyc' -delete
find . -name '__pycache__' -exec rm -rf {} +

find /r2e_tests -name '*.pyc' -delete
find /r2e_tests -name '__pycache__' -exec rm -rf {} +

export REPO_PATH="/testbed"
REPO_PATH="/testbed"
ALT_PATH="/root"
R2E_ORIGINAL_DIR="/swe_util/r2e_original"
SKIP_FILES_NEW=("run_tests.sh" "r2e_tests")
for skip_file in "${SKIP_FILES_NEW[@]}"; do
    if [ -e "$REPO_PATH/$skip_file" ]; then
        mv "$REPO_PATH/$skip_file" "$ALT_PATH/$skip_file"
    fi
done

mv /r2e_tests "$ALT_PATH/r2e_tests"
ln -s "$ALT_PATH/r2e_tests" /r2e_tests
rm -rf "$R2E_ORIGINAL_DIR"
mkdir -p "$R2E_ORIGINAL_DIR"
cp -a "$ALT_PATH/run_tests.sh" "$R2E_ORIGINAL_DIR/run_tests.sh"
cp -a "$ALT_PATH/r2e_tests" "$R2E_ORIGINAL_DIR/r2e_tests"
chmod -R a-w "$R2E_ORIGINAL_DIR"
ln -s "$ALT_PATH/r2e_tests" "$REPO_PATH/r2e_tests"
