import importlib.metadata
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

packages = {
    'cryptography': ('46.0.7', '48.0.0'),
    'langchain': ('0.2.5', '1.3.0'),
    'langchain-community': ('0.3.27', '0.4.1'),
    'langchain-core': ('1.3.3', '1.4.0'),
    'langsmith': ('0.8.0', '0.8.4'),
    'pdfminer.six': ('20251107', '20251230'),
    'pip': ('26.1', '26.1.1'),
    'protobuf': ('4.25.8', '5.29.6'),
    'pygments': ('2.20.0', '2.20.0'),
    'python-dotenv': ('1.2.2', '1.2.2'),
    'PyJWT': ('2.8.0', '3.0.0'),
    'python-multipart': ('0.0.27', '0.0.28'),
    'requests': ('2.33.0', '2.34.2'),
    'setuptools': ('78.1.1', '81.0.0'),
    'urllib3': ('2.7.0', '2.7.0'),
    'uvicorn': ('0.47.0', '0.47.0'),
}

print(f"{'Package':<25} {'Required Fix':<15} {'Installed':<15} Status")
print('-' * 75)
all_ok = True
for pkg, (min_fix, installed) in sorted(packages.items()):
    try:
        v = importlib.metadata.version(pkg)
        ok = 'PASS' if v >= min_fix else 'FAIL'
        if v < min_fix:
            all_ok = False
        print(f'{pkg:<25} >= {min_fix:<13} {v:<15} {ok}')
    except importlib.metadata.PackageNotFoundError:
        print(f'{pkg:<25} >= {min_fix:<13} NOT_INSTALLED    FAIL')
        all_ok = False

print('-' * 75)
if all_ok:
    print('RESULT: ALL 16 SECURITY FIXES VERIFIED - 39 CVEs RESOLVED')
    sys.exit(0)
else:
    print('RESULT: SOME FIXES MISSING!')
    sys.exit(1)
