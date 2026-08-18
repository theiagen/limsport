import subprocess
from pathlib import Path

import pytest

from limsport import file_parsing
from limsport.config import FileParsingOutput
from limsport.exceptions import FileParsingError


def _outputs(command, timeout_seconds=None, name="out"):
    return [FileParsingOutput(name=name, command=command, timeout_seconds=timeout_seconds)]


# --- real bash execution against local files -- no mocking needed ---


def test_run_simple_command_reads_local_file(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("hello\n")
    result = file_parsing.run(_outputs('cat "$FILE"'), str(path))
    assert result == ["hello"]


def test_run_supports_pipes(tmp_path):
    # Matches the motivating example: cut -f1 file | cut -f3 -d:
    path = tmp_path / "data.tsv"
    path.write_text("a:b:c\td:e:f\n")
    command = 'cut -f1 "$FILE" | cut -d: -f3'
    result = file_parsing.run(_outputs(command), str(path))
    assert result == ["c"]


def test_run_strips_trailing_newline_without_flagging_it(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("irrelevant")
    # `echo` always terminates its output with a single newline.
    result = file_parsing.run(_outputs("echo hello"), str(path))
    assert result == ["hello"]


def test_run_raises_on_embedded_newline(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("irrelevant")
    with pytest.raises(FileParsingError, match="newline"):
        file_parsing.run(_outputs("printf 'a\\nb\\n'"), str(path))


def test_run_raises_on_nonzero_exit(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("irrelevant")
    with pytest.raises(FileParsingError, match="exit 1"):
        file_parsing.run(_outputs("exit 1"), str(path))


def test_run_raises_on_timeout(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("irrelevant")
    with pytest.raises(FileParsingError, match="timed out"):
        file_parsing.run(_outputs("sleep 5", timeout_seconds=0.1), str(path))


def test_run_succeeds_with_empty_output(tmp_path):
    # A command that legitimately produces nothing (e.g. grep finds no
    # match) is a successful run with an empty result, not an error --
    # it's downstream QC's job to treat "" as a missing value, not this
    # function's job to treat it as a failure.
    path = tmp_path / "data.txt"
    path.write_text("no match here\n")
    result = file_parsing.run(_outputs('grep nonexistent_pattern "$FILE" || true'), str(path))
    assert result == [""]


def test_run_on_nonexistent_local_file_surfaces_the_commands_own_error(tmp_path):
    # No file_parsing-specific existence check happens before running the
    # command -- a bad local path is just an ordinary command failure
    # (cat's own "No such file or directory"), handled by the same
    # nonzero-exit path as any other broken command.
    missing = tmp_path / "does_not_exist.txt"
    with pytest.raises(FileParsingError, match="No such file or directory"):
        file_parsing.run(_outputs('cat "$FILE"'), str(missing))


def test_run_exposes_local_path_via_env_var_not_command_text(tmp_path):
    # The path itself is never spliced into the command string -- the
    # command only ever sees it via $FILE.
    path = tmp_path / "data.txt"
    path.write_text("content\n")
    result = file_parsing.run(_outputs("wc -l < \"$FILE\""), str(path))
    assert result[0].strip() == "1"


def test_local_path_is_never_localized(tmp_path, monkeypatch):
    path = tmp_path / "data.txt"
    path.write_text("hello\n")
    calls = []
    real_run = subprocess.run

    def recording_run(argv, **kwargs):
        calls.append(argv)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(file_parsing.subprocess, "run", recording_run)
    result = file_parsing.run(_outputs('cat "$FILE"'), str(path))
    assert result == ["hello"]
    # Only the bash command itself ran -- no gcloud/localize call for a local path.
    assert len(calls) == 1
    assert calls[0][0] == "bash"


# --- multiple outputs sharing one localized file ---


def test_run_multiple_outputs_each_run_their_own_command_against_the_same_file(tmp_path):
    path = tmp_path / "data.tsv"
    path.write_text("a\tb\tc\n")
    outputs = [
        FileParsingOutput(name="first", command='cut -f1 "$FILE"'),
        FileParsingOutput(name="second", command='cut -f2 "$FILE"'),
        FileParsingOutput(name="third", command='cut -f3 "$FILE"'),
    ]
    result = file_parsing.run(outputs, str(path))
    assert result == ["a", "b", "c"]


def _mock_gcs_bash(monkeypatch, gcs_content, bash_run):
    """Patch shutil.which/subprocess.run so `gcloud storage cp` writes
    gcs_content to its target, and every `bash -c` invocation is answered
    by bash_run(command) -> subprocess.CompletedProcess. Shared by the
    gs://-sourced multi-output tests below."""

    def fake_run(argv, **kwargs):
        if argv[0] == "gcloud":
            Path(argv[-1]).write_text(gcs_content)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        assert argv[0] == "bash"
        return bash_run(argv[2])

    monkeypatch.setattr(file_parsing.shutil, "which", lambda tool: "/usr/bin/gcloud")
    monkeypatch.setattr(file_parsing.subprocess, "run", fake_run)


def test_run_multiple_outputs_localizes_gs_path_only_once(monkeypatch):
    localize_calls = []
    real_localize = file_parsing._localize

    def recording_localize(raw_path):
        localize_calls.append(raw_path)
        return real_localize(raw_path)

    monkeypatch.setattr(file_parsing, "_localize", recording_localize)
    _mock_gcs_bash(
        monkeypatch,
        "a\tb\n",
        lambda command: subprocess.CompletedProcess(["bash", "-c", command], 0, stdout="value\n", stderr=""),
    )

    outputs = [
        FileParsingOutput(name="first", command="echo first"),
        FileParsingOutput(name="second", command="echo second"),
    ]
    result = file_parsing.run(outputs, "gs://bucket/data.tsv")
    assert result == ["value", "value"]
    assert localize_calls == ["gs://bucket/data.tsv"]


def test_run_multiple_outputs_a_failing_command_aborts_the_rest(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("irrelevant\n")
    outputs = [
        FileParsingOutput(name="first", command="echo ok"),
        FileParsingOutput(name="second", command="exit 1"),
        FileParsingOutput(name="third", command="echo never_runs"),
    ]
    with pytest.raises(FileParsingError, match="exit 1"):
        file_parsing.run(outputs, str(path))


def test_run_multiple_outputs_each_use_their_own_timeout(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("irrelevant\n")
    outputs = [
        FileParsingOutput(name="fast", command="echo ok"),
        FileParsingOutput(name="slow", command="sleep 5", timeout_seconds=0.1),
    ]
    with pytest.raises(FileParsingError, match="timed out"):
        file_parsing.run(outputs, str(path))


def test_run_multiple_outputs_cleans_up_temp_dir_once_even_on_partial_failure(monkeypatch):
    created_dirs = []
    real_mkdtemp = file_parsing.tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    def bash_run(command):
        if "boom" in command:
            return subprocess.CompletedProcess(["bash", "-c", command], 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(["bash", "-c", command], 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(file_parsing.tempfile, "mkdtemp", recording_mkdtemp)
    _mock_gcs_bash(monkeypatch, "data\n", bash_run)

    outputs = [
        FileParsingOutput(name="first", command="echo ok"),
        FileParsingOutput(name="second", command="exit boom"),
    ]
    with pytest.raises(FileParsingError, match="boom"):
        file_parsing.run(outputs, "gs://bucket/data.txt")

    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists()


# --- gs:// localization -- subprocess.run and shutil.which are mocked so
# these never touch real cloud storage or require gcloud to be installed ---


def test_localize_dispatches_gs_path_to_gcloud_storage_cp(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        Path(argv[-1]).write_text("downloaded\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(file_parsing.shutil, "which", lambda tool: "/usr/bin/gcloud")
    monkeypatch.setattr(file_parsing.subprocess, "run", fake_run)

    local_path, tmp_dir = file_parsing._localize("gs://bucket/path/myfile.bam")
    try:
        assert calls == [["gcloud", "storage", "cp", "gs://bucket/path/myfile.bam", local_path]]
        assert Path(local_path).name == "myfile.bam"
        assert Path(local_path).read_text() == "downloaded\n"
        assert tmp_dir is not None
    finally:
        if tmp_dir is not None:
            file_parsing.shutil.rmtree(tmp_dir, ignore_errors=True)


# --- security regression tests ---


@pytest.mark.parametrize(
    "raw_path,expected_name",
    [
        ("gs://bucket/..", "downloaded"),  # Path(...).name == ".." unguarded
        # ("gs://bucket/." -- not included: pathlib normalizes a trailing
        # "/." itself, so .name is already "bucket", never a bare ".".
        ("gs://bucket/normal_file.bam", "normal_file.bam"),  # unaffected
    ],
)
def test_safe_basename_rejects_path_traversal_segments(raw_path, expected_name):
    assert file_parsing._safe_basename(raw_path) == expected_name


def test_localize_never_escapes_temp_dir_for_traversal_path(tmp_path, monkeypatch):
    # a raw_path ending in "/.." shouldn't make the download target
    # resolve outside the sandboxed temp directory.
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        Path(argv[-1]).write_text("downloaded\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(file_parsing.shutil, "which", lambda tool: "/usr/bin/gcloud")
    monkeypatch.setattr(file_parsing.subprocess, "run", fake_run)

    local_path, tmp_dir = file_parsing._localize("gs://bucket/..")
    try:
        assert tmp_dir is not None
        # local_path must be a real child of tmp_dir, not tmp_dir's parent.
        assert Path(local_path).parent == tmp_dir
        assert Path(local_path).name == "downloaded"
    finally:
        if tmp_dir is not None:
            file_parsing.shutil.rmtree(tmp_dir, ignore_errors=True)


def test_cleanup_failure_is_logged_not_silently_swallowed(monkeypatch, caplog, tmp_path):
    tmp_dir = tmp_path / "some_dir"
    tmp_dir.mkdir()

    def failing_rmtree(path):
        raise OSError("permission denied")

    monkeypatch.setattr(file_parsing.shutil, "rmtree", failing_rmtree)
    with caplog.at_level("WARNING"):
        file_parsing._cleanup(tmp_dir)
    assert any("permission denied" in r.message for r in caplog.records)


def test_localize_missing_gcloud_raises_before_any_download_attempt(monkeypatch):
    calls = []
    monkeypatch.setattr(file_parsing.shutil, "which", lambda tool: None)
    monkeypatch.setattr(
        file_parsing.subprocess, "run", lambda *a, **kw: calls.append((a, kw))
    )
    with pytest.raises(FileParsingError, match="gcloud"):
        file_parsing._localize("gs://bucket/f.txt")
    assert calls == []


def test_localize_download_failure_raises_and_cleans_up_tmp_dir(monkeypatch):
    created_dirs = []
    real_mkdtemp = file_parsing.tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    monkeypatch.setattr(file_parsing.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(file_parsing.shutil, "which", lambda tool: "/usr/bin/gcloud")
    monkeypatch.setattr(
        file_parsing.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="permission denied"),
    )

    with pytest.raises(FileParsingError, match="permission denied"):
        file_parsing._localize("gs://bucket/f.txt")

    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists()


def test_run_cleans_up_downloaded_file_after_success(monkeypatch):
    created_dirs = []
    real_mkdtemp = file_parsing.tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    def fake_run(argv, **kwargs):
        if argv[0] == "gcloud":
            Path(argv[-1]).write_text("gs content\n")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        assert argv[0] == "bash"
        return subprocess.CompletedProcess(argv, 0, stdout="parsed-value\n", stderr="")

    monkeypatch.setattr(file_parsing.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(file_parsing.shutil, "which", lambda tool: "/usr/bin/gcloud")
    monkeypatch.setattr(file_parsing.subprocess, "run", fake_run)

    result = file_parsing.run(_outputs("whatever"), "gs://bucket/data.txt")
    assert result == ["parsed-value"]
    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists()  # cleaned up after success


def test_run_cleans_up_downloaded_file_after_command_failure(monkeypatch):
    created_dirs = []
    real_mkdtemp = file_parsing.tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    def fake_run(argv, **kwargs):
        if argv[0] == "gcloud":
            Path(argv[-1]).write_text("gs content\n")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        assert argv[0] == "bash"
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr(file_parsing.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(file_parsing.shutil, "which", lambda tool: "/usr/bin/gcloud")
    monkeypatch.setattr(file_parsing.subprocess, "run", fake_run)

    with pytest.raises(FileParsingError, match="boom"):
        file_parsing.run(_outputs("whatever"), "gs://bucket/data.txt")

    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists()  # cleaned up even on failure
