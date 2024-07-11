import os
from pathlib import Path



if __name__ == "__main__":
    file = Path("file.py")
    print(os.path.getctime(file))
    print(file.stat().st_ctime)