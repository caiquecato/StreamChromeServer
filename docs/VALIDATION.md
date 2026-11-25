# Release 1.0.0 validation

Validation was completed on Windows x64 on November 24, 2026.

- The Python suite ran 63 tests without failures; one symlink test was skipped because Windows symlink privileges were unavailable. All JavaScript test suites passed.
- The public installer was installed in both portable and standard modes. Each run downloaded the pinned upstream components, preserved an existing file in the chosen destination, and launched the compiled GUI executable without a console.
- FFmpeg and FFprobe were detected. Jellyfin reached its healthy endpoint. A dummy subtitle credential was stored in the selected data directory without its plaintext value appearing in the saved file.
- Exit stopped the application and Jellyfin in both modes. Standard uninstallation removed its registration. Disposable installation directories and test data were removed, and the preexisting shortcut was preserved.
- Browser checks covered all six catalog layouts at widths of 390, 768, 1024 and 1440 pixels, nested episode sources, season selection, actual local playback and the floating miniplayer. README screenshots show a fictional demonstration library.
- Independent security, design and release-lifecycle reviews were completed. Reported defects were corrected and rechecked. A pattern-based secret scan of the eight existing repository commits and the intended publication files found only dummy credentials in regression tests.

The native installation check caught an explicit destination being overwritten in silent portable mode. The installer was corrected and both modes were rerun successfully before publication. A slow FFmpeg download origin was replaced with the vendor's GitHub release of the identical file, retaining its SHA-256 pin.

These checks establish the observed behavior in this environment. They do not establish compatibility with every codec, addon, source provider, hardware configuration or Windows security policy, nor prove the absence of every possible defect or secret.

The original external-drive media library was not used for release validation. Public assets contain the online setup executable and its checksum; local offline engine bundles are excluded.
