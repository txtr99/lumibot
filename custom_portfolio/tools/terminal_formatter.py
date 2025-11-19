"""
Terminal Output Formatter - Scientific Design System

Implements ColorBrewer-based semantic color palette with Gestalt principles
for optimal readability on both dark and light terminal backgrounds.

Design Principles:
- 5-color semantic palette (success/error/warning/info/neutral)
- 3-level typographic hierarchy
- Whitespace-based grouping (no decorative boxes)
- 80-character maximum line length
- WCAG AA accessible contrast ratios
"""

from enum import Enum
from typing import List, Optional


class Color(Enum):
    """Semantic color codes following ColorBrewer Set2 palette."""

    # ANSI 16-color codes (widely supported)
    SUCCESS = "\033[32m"  # Green - success, completion
    ERROR = "\033[31m"  # Red - errors, failures
    WARNING = "\033[33m"  # Yellow - warnings, degraded states
    INFO = "\033[36m"  # Cyan - key labels, data identifiers
    NEUTRAL = "\033[37m"  # White - body text
    DIM = "\033[2;37m"  # Dim gray - metadata, timestamps
    BOLD = "\033[1m"  # Bold - headers only
    RESET = "\033[0m"  # Reset to default


class Symbol(Enum):
    """Semantic symbols (screen-reader compatible)."""

    SUCCESS = "✓"
    ERROR = "×"
    WARNING = "!"
    BULLET = "•"


class TerminalFormatter:
    """Format terminal output following scientific design principles."""

    # Layout constants
    MAX_LINE_LENGTH = 80
    HORIZONTAL_RULE = "─" * MAX_LINE_LENGTH
    INDENT = "  "  # 2-space standard indent

    @staticmethod
    def colorize(text: str, color: Color) -> str:
        """Apply semantic color to text.

        Args:
            text: Text to colorize
            color: Semantic color from Color enum

        Returns:
            ANSI-colored text with reset code
        """
        return f"{color.value}{text}{Color.RESET.value}"

    @classmethod
    def section_header(cls, text: str) -> str:
        """Level 1 header: UPPERCASE BOLD.

        Args:
            text: Header text

        Returns:
            Formatted section header
        """
        formatted = text.upper()
        return cls.colorize(formatted, Color.BOLD)

    @classmethod
    def subsection_header(cls, text: str) -> str:
        """Level 2 header: Title Case.

        Args:
            text: Header text

        Returns:
            Formatted subsection header
        """
        return text  # Normal weight, no color

    @classmethod
    def label(cls, text: str) -> str:
        """Level 3 label: Sentence case with info color.

        Args:
            text: Label text

        Returns:
            Formatted label
        """
        return cls.colorize(text, Color.INFO)

    @classmethod
    def metadata(cls, text: str) -> str:
        """Dim text for metadata, timestamps, paths.

        Args:
            text: Metadata text

        Returns:
            Dimmed text
        """
        return cls.colorize(text, Color.DIM)

    @classmethod
    def success(cls, text: str, symbol: bool = True) -> str:
        """Success message with optional symbol.

        Args:
            text: Message text
            symbol: Whether to prepend ✓ symbol

        Returns:
            Green success message
        """
        prefix = f"{Symbol.SUCCESS.value} " if symbol else ""
        return cls.colorize(f"{prefix}{text}", Color.SUCCESS)

    @classmethod
    def error(cls, text: str, symbol: bool = True) -> str:
        """Error message with optional symbol.

        Args:
            text: Message text
            symbol: Whether to prepend × symbol

        Returns:
            Red error message
        """
        prefix = f"{Symbol.ERROR.value} " if symbol else ""
        return cls.colorize(f"{prefix}{text}", Color.ERROR)

    @classmethod
    def warning(cls, text: str, symbol: bool = True) -> str:
        """Warning message with optional symbol.

        Args:
            text: Message text
            symbol: Whether to prepend ! symbol

        Returns:
            Yellow warning message
        """
        prefix = f"{Symbol.WARNING.value} " if symbol else ""
        return cls.colorize(f"{prefix}{text}", Color.WARNING)

    @classmethod
    def horizontal_rule(cls) -> str:
        """80-character horizontal separator.

        Returns:
            Horizontal rule
        """
        return cls.HORIZONTAL_RULE

    @classmethod
    def table(cls, headers: List[str], rows: List[List[str]], col_widths: Optional[List[int]] = None) -> str:
        """Format pipe-delimited table with aligned columns.

        Args:
            headers: Column headers
            rows: Data rows
            col_widths: Optional column widths (auto-calculated if None)

        Returns:
            Formatted table string
        """
        # Calculate column widths if not provided
        if col_widths is None:
            col_widths = [len(h) for h in headers]
            for row in rows:
                for i, cell in enumerate(row):
                    if i < len(col_widths):
                        col_widths[i] = max(col_widths[i], len(str(cell)))

        # Format header row
        header_parts = []
        for i, header in enumerate(headers):
            width = col_widths[i] if i < len(col_widths) else len(header)
            header_parts.append(header.ljust(width))
        header_line = " | ".join(header_parts)

        # Format separator
        separator_parts = ["-" * w for w in col_widths[: len(headers)]]
        separator_line = "-|-".join(separator_parts)

        # Format data rows
        data_lines = []
        for row in rows:
            row_parts = []
            for i, cell in enumerate(row):
                width = col_widths[i] if i < len(col_widths) else len(str(cell))
                # Right-align numbers, left-align text
                cell_str = str(cell)
                if cell_str.replace(".", "").replace(",", "").replace("$", "").replace("-", "").isdigit():
                    row_parts.append(cell_str.rjust(width))
                else:
                    row_parts.append(cell_str.ljust(width))
            data_lines.append(" | ".join(row_parts))

        # Combine all parts
        return "\n".join([header_line, separator_line] + data_lines)

    @classmethod
    def bullet_list(cls, items: List[str], indent: int = 0) -> str:
        """Format bullet list with • symbol.

        Args:
            items: List items
            indent: Indentation level (multiples of 2 spaces)

        Returns:
            Formatted bullet list
        """
        indent_str = cls.INDENT * indent
        lines = [f"{indent_str}{Symbol.BULLET.value} {item}" for item in items]
        return "\n".join(lines)

    @classmethod
    def key_value(cls, key: str, value: str, indent: int = 1) -> str:
        """Format key-value pair with colored key.

        Args:
            key: Key label
            value: Value text
            indent: Indentation level

        Returns:
            Formatted key-value line
        """
        indent_str = cls.INDENT * indent
        colored_key = cls.label(f"{key}:")
        return f"{indent_str}{colored_key} {value}"

    @classmethod
    def wrap_text(cls, text: str, max_width: int = MAX_LINE_LENGTH, indent: int = 0, hanging: int = 0) -> str:
        """Wrap text to maximum width with optional hanging indent.

        Args:
            text: Text to wrap
            max_width: Maximum line width
            indent: Initial indent level
            hanging: Additional indent for wrapped lines

        Returns:
            Wrapped text
        """
        words = text.split()
        lines = []
        current_line = []
        current_length = indent * 2

        indent_str = cls.INDENT * indent
        hanging_str = cls.INDENT * (indent + hanging)

        for word in words:
            word_length = len(word)
            if current_length + word_length + 1 <= max_width:
                current_line.append(word)
                current_length += word_length + 1
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_length = (indent + hanging) * 2 + word_length

        if current_line:
            lines.append(" ".join(current_line))

        # Apply indentation
        if lines:
            result = indent_str + lines[0]
            if len(lines) > 1:
                result += "\n" + "\n".join(hanging_str + line for line in lines[1:])
            return result
        return ""
