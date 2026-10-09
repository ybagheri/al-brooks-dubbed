"""Phase 1 and 2 pipeline for the Al Brooks dubbing project.

The package is intentionally split into small, focused modules:

Phase 1 - extraction and transcription:

* :mod:`src.config`        - configuration and environment validation
* :mod:`src.discovery`     - input video discovery and validation
* :mod:`src.media`         - FFmpeg / FFprobe media inspection and extraction
* :mod:`src.transcription` - Groq speech-to-text client
* :mod:`src.outputs`       - output serialization and validation
* :mod:`src.pipeline`      - stage orchestration
* :mod:`src.cli`           - command line interface

Phase 2 - transcript quality assurance and preparation:

* :mod:`src.text_cleaning` - deterministic, formatting-only cleanup
* :mod:`src.qa`            - quality detection and reporting
* :mod:`src.transcript`    - raw and prepared transcript models and schemas
* :mod:`src.terminology`   - Al Brooks vocabulary annotation
* :mod:`src.verification`  - optional bounded re-transcription check
* :mod:`src.preparation`   - preparation stage orchestration

Shared:

* :mod:`src.logging_utils` - logging with secret redaction
* :mod:`src.errors`        - exception hierarchy
* :mod:`src.enums`         - Python 3.10 compatible string enums
"""

__all__ = ["__version__"]

__version__ = "0.3.0"
