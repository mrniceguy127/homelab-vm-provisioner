from types import SimpleNamespace


def completed_process(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout)
