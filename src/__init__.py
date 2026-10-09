"""Phase 1 pipeline for the Al Brooks dubbing project.

The package is intentionally split into small, focused modules:

* :mod:`src.config`        - configuration and environment validation
* :mod:`src.discovery`     - input video discovery and validation
* :mod:`src.media`         - FFmpeg / FFprobe media inspection and extraction
* :mod:`src.transcription` - Groq speech-to-text client
* :mod:`src.outputs`       - output serialization and validation
* :mod:`src.pipeline`      - stage orchestration
* :mod:`src.cli`           - command line interface
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
