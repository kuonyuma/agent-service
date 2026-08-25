"""文件工具共用的工作区路径隔离策略。"""

from __future__ import annotations

import ntpath
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


class WorkspacePathError(ValueError):
    """模型提供的路径不满足工作区安全约束。"""


@dataclass(frozen=True, slots=True)
class WorkspacePathPolicy:
    """把文件工具限制在一个可信根目录内。

    根目录由服务端配置，模型只能提供相对路径。策略拒绝符号链接、Windows
    junction 等重解析点，避免通过工作区中的链接跳转到外部位置。
    """

    root: Path
    max_read_bytes: int = 1_048_576
    max_write_bytes: int = 1_048_576
    max_output_bytes: int = 1_048_576
    max_list_entries: int = 1_000

    def __post_init__(self) -> None:
        if (
            min(
                self.max_read_bytes,
                self.max_write_bytes,
                self.max_output_bytes,
                self.max_list_entries,
            )
            <= 0
        ):
            raise ValueError("工作区资源上限必须大于 0。")

        configured_root = Path(self.root)
        try:
            configured_root.mkdir(parents=True, exist_ok=True)
            root_status = configured_root.lstat()
        except OSError as exc:
            raise WorkspacePathError(f"无法创建或访问工作区根目录：{exc}") from exc
        if self._is_link_or_reparse(configured_root, root_status):
            raise WorkspacePathError("工作区根目录不能是符号链接或重解析点。")
        if not stat.S_ISDIR(root_status.st_mode):
            raise WorkspacePathError("工作区根路径不是目录。")

        try:
            resolved_root = configured_root.resolve(strict=True)
        except OSError as exc:
            raise WorkspacePathError(f"无法解析工作区根目录：{exc}") from exc
        object.__setattr__(self, "root", resolved_root)

    def resolve_existing(
        self,
        raw_path: object,
        *,
        expected: str = "any",
    ) -> Path:
        """解析已存在路径，并确认其类型和所有组件均安全。"""

        parts = self._relative_parts(raw_path)
        candidate = self.root.joinpath(*parts)
        self._check_existing_components(parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved_status = resolved.lstat()
        except FileNotFoundError as exc:
            raise WorkspacePathError("工作区内不存在该路径。") from exc
        except OSError as exc:
            raise WorkspacePathError(f"无法访问工作区路径：{exc}") from exc

        self._ensure_contained(resolved)
        if self._is_link_or_reparse(resolved, resolved_status):
            raise WorkspacePathError("不允许访问符号链接或重解析点。")
        if expected == "file" and not stat.S_ISREG(resolved_status.st_mode):
            raise WorkspacePathError("指定路径不是普通文件。")
        if expected == "directory" and not stat.S_ISDIR(resolved_status.st_mode):
            raise WorkspacePathError("指定路径不是目录。")
        if expected not in {"any", "file", "directory"}:
            raise ValueError(f"未知的路径类型约束：{expected}")
        return resolved

    def resolve_for_write(self, raw_path: object) -> Path:
        """解析写入目标，在根目录内逐层创建缺失的父目录。"""

        parts = self._relative_parts(raw_path)
        if not parts:
            raise WorkspacePathError("写入路径不能为空或指向工作区根目录。")

        current = self.root
        for part in parts[:-1]:
            current /= part
            current_status = self._try_lstat(current)
            if current_status is None:
                try:
                    current.mkdir()
                    current_status = current.lstat()
                except OSError as exc:
                    raise WorkspacePathError(f"无法创建工作区子目录：{exc}") from exc
            if self._is_link_or_reparse(current, current_status):
                raise WorkspacePathError("路径包含符号链接或 Windows 重解析点。")
            if not stat.S_ISDIR(current_status.st_mode):
                raise WorkspacePathError("写入路径的父级不是目录。")
            self._ensure_contained(self._resolve_strict(current))

        candidate = current / parts[-1]
        target_status = self._try_lstat(candidate)
        if target_status is not None:
            if self._is_link_or_reparse(candidate, target_status):
                raise WorkspacePathError("写入目标不能是符号链接或重解析点。")
            if not stat.S_ISREG(target_status.st_mode):
                raise WorkspacePathError("写入目标不是普通文件。")

        self._ensure_contained(self._resolve_strict(current))
        return candidate

    def read_text(self, path: Path) -> str:
        """以固定字节上限读取 UTF-8 文件。"""

        try:
            with path.open("rb") as file:
                content = file.read(self.max_read_bytes + 1)
        except OSError as exc:
            raise WorkspacePathError(f"读取文件失败：{exc}") from exc
        if len(content) > self.max_read_bytes:
            raise WorkspacePathError(f"文件超过读取上限 {self.max_read_bytes} 字节。")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspacePathError("文件不是有效的 UTF-8 文本。") from exc
        self.ensure_output(text)
        return text

    def validate_write_content(self, content: str) -> None:
        """限制单次文件写入的数据量。"""

        if len(content.encode("utf-8")) > self.max_write_bytes:
            raise WorkspacePathError(f"写入内容超过上限 {self.max_write_bytes} 字节。")

    def ensure_output(self, content: str) -> None:
        """限制返回给模型的单次文本大小。"""

        if len(content.encode("utf-8")) > self.max_output_bytes:
            raise WorkspacePathError(f"工具输出超过上限 {self.max_output_bytes} 字节。")

    def list_directory(self, path: Path) -> list[str]:
        """列出目录但不跟随链接，并隐藏敏感名称。"""

        items: list[str] = []
        scanned = 0
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > self.max_list_entries:
                        raise WorkspacePathError(
                            f"目录条目超过上限 {self.max_list_entries} 个。"
                        )
                    if self._is_sensitive_part(entry.name):
                        continue
                    entry_status = entry.stat(follow_symlinks=False)
                    entry_path = Path(entry.path)
                    if self._is_link_or_reparse(entry_path, entry_status):
                        items.append(f"{entry.name} [链接已屏蔽]")
                    elif stat.S_ISDIR(entry_status.st_mode):
                        items.append(entry.name + "/")
                    else:
                        items.append(entry.name)
        except WorkspacePathError:
            raise
        except OSError as exc:
            raise WorkspacePathError(f"列出目录失败：{exc}") from exc

        items.sort(key=str.casefold)
        self.ensure_output("\n".join(items))
        return items

    def display_path(self, path: Path) -> str:
        """返回不暴露宿主机根目录的相对显示路径。"""

        relative = path.relative_to(self.root)
        return "." if not relative.parts else relative.as_posix()

    def _relative_parts(self, raw_path: object) -> tuple[str, ...]:
        if not isinstance(raw_path, str):
            raise WorkspacePathError("路径必须是字符串。")
        if "\x00" in raw_path:
            raise WorkspacePathError("路径包含非法空字符。")

        raw = raw_path.strip()
        if not raw:
            raise WorkspacePathError("路径不能为空。")
        windows_path = PureWindowsPath(raw)
        posix_path = PurePosixPath(raw)
        if (
            windows_path.drive
            or windows_path.root
            or posix_path.root
            or raw.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./"))
        ):
            raise WorkspacePathError("只允许工作区内的相对路径。")

        normalized = raw.replace("\\", "/")
        parts = tuple(
            part for part in PurePosixPath(normalized).parts if part not in {"", "."}
        )
        for part in parts:
            if part == "..":
                raise WorkspacePathError("路径不能包含上级目录 '..'。")
            if ":" in part:
                raise WorkspacePathError("路径不能包含盘符或 NTFS 备用数据流。")
            if ntpath.isreserved(part):
                raise WorkspacePathError(f"路径包含 Windows 保留名称：{part}")
            if self._is_sensitive_part(part):
                raise WorkspacePathError(f"不允许访问敏感路径段：{part}")
        return parts

    def _check_existing_components(self, parts: tuple[str, ...]) -> None:
        current = self.root
        for part in parts:
            current /= part
            current_status = self._try_lstat(current)
            if current_status is None:
                return
            if self._is_link_or_reparse(current, current_status):
                raise WorkspacePathError("路径包含符号链接或 Windows 重解析点。")

    def _ensure_contained(self, path: Path) -> None:
        if not path.is_relative_to(self.root):
            raise WorkspacePathError("路径解析后超出了工作区范围。")

    @staticmethod
    def _is_sensitive_part(part: str) -> bool:
        normalized = part.casefold()
        return normalized in {".git", ".venv", ".env", "config.yaml"} or (
            normalized.startswith(".env.")
        )

    @staticmethod
    def _try_lstat(path: Path) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WorkspacePathError(f"无法检查工作区路径：{exc}") from exc

    @staticmethod
    def _resolve_strict(path: Path) -> Path:
        try:
            return path.resolve(strict=True)
        except OSError as exc:
            raise WorkspacePathError(f"无法解析工作区路径：{exc}") from exc

    @staticmethod
    def _is_link_or_reparse(path: Path, path_status: os.stat_result) -> bool:
        attributes = getattr(path_status, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        is_junction = getattr(path, "is_junction", lambda: False)
        return (
            stat.S_ISLNK(path_status.st_mode)
            or bool(attributes & reparse_flag)
            or bool(is_junction())
        )
