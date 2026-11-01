"""Сборка отдельного PGO-кандидата по проверенному локальному профилю."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(profile, manifest, compiler):
    if platform.system() != 'Darwin' or platform.machine() != manifest['architecture']:
        raise ValueError('Этот профиль проверен только на macOS с архитектурой ' + manifest['architecture'])
    if digest(profile) != manifest['portable_profile_sha256']:
        raise ValueError('Хэш PGO-профиля не совпадает с manifest')
    for name, expected in manifest['input_hashes'].items():
        if digest(ROOT / 'vendor' / name) != expected:
            raise ValueError('Исходники изменились; нужно заново обучить профиль: ' + name)
    version = subprocess.check_output([compiler, '--version'], text=True)
    if version != manifest['compiler']:
        raise ValueError('Версия компилятора отличается от обучающей; нужно заново обучить профиль')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--profile', type=Path, default=ROOT / 'search-results/pgo-profile.profdata')
    ap.add_argument('--manifest', type=Path, default=ROOT / 'search-results/pgo-implementation-manifest.json')
    ap.add_argument('--out', type=Path, default=ROOT / 'vendor/engine-pgo.dylib')
    ap.add_argument('--compiler', default='clang++')
    args = ap.parse_args()
    executable = shutil.which(args.compiler)
    if executable is None:
        raise ValueError('Компилятор не найден: ' + args.compiler)
    # Закрепляем cwd, сохраняя имя clang++ при наличии symlink на clang.
    compiler = str(Path(executable).absolute())
    profile = args.profile.resolve()
    manifest = json.loads(args.manifest.read_text())
    validate(profile, manifest, compiler)
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pgo-build-', dir=output.parent) as scratch:
        candidate = Path(scratch) / output.name
        # Относительное имя исходника совпадает с переносимыми ключами профиля.
        subprocess.run([compiler, '-O3', '-std=c++11', '-dynamiclib',
                        '-install_name', '@rpath/' + output.name,
                        '-Werror=profile-instr-out-of-date', '-Werror=profile-instr-unprofiled',
                        '-fprofile-use=' + str(profile), 'engine_adapted.cpp',
                        '-o', str(candidate)], cwd=ROOT / 'vendor', check=True)
        # Не заменять старую библиотеку, если inputs изменились во время сборки.
        validate(profile, manifest, compiler)
        os.replace(candidate, output)
    print('PGO-кандидат собран: ' + str(output))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
