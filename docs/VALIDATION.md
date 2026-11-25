# Release validation

## Release 1.0.0: completed historical validation

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

## Release 1.1.0 validation

Validation was completed on Windows x64 on November 25, 2026. The earlier 1.0.0 results remain historical evidence.

- The final Python suite ran 94 tests without failures; one symlink test was skipped because Windows symlink privileges were unavailable. All JavaScript suites passed, including a regression that prevents the update page from reloading while the old server is still shutting down.
- The public 1.1.0 setup was installed in both portable and standard modes with optional integration tasks off. Native checks covered English library folders, the compiled integration CLI, Settings API, a live GitHub update check, FFmpeg/FFprobe, encrypted subtitle settings, Jellyfin health, Exit and standard uninstallation. Preexisting test files and the existing shortcut were preserved. Test installations and uninstall registrations were removed.
- A separate native check exercised the packaged update helper with the release executable: replacement, restart, retained local-only mode and ports, preserved media/settings sentinels, and removal of staging/backup files. Its disposable installation was removed. Download validation and native replacement were exercised separately; this was not a live upgrade to a later public version.
- Nine integration tests used a fake Windows API and temporary volume directories to cover registration, drive-letter changes, executable hashes, preserving opt-in during upgrades, icon configuration restoration and conflict refusal. Physical USB disconnect/reconnect behavior and Explorer icon caching were not tested on hardware. The native configuration CLI was checked with icon customization disabled, so no real drive icon or startup watcher was installed during validation.
- Legacy Movies/Filmes scanning tests retained existing relative paths, identifiers and watch progress. Native destination tests preserved existing files. A full 1.0.0-to-1.1.0 upgrade of a personal library was not performed.
- Browser checks covered the three discovery selectors, Settings, addon and Exit dialogs, desktop/narrow layouts, keyboard focus and absence of horizontal overflow at 390 pixels. Figures 14–17 use the fictional demonstration library and generic demonstration paths.
- Independent security, interface and release reviews were completed. The custom Jellyfin tunnel port, return-to-default port configuration and portable registration refresh findings were fixed and rechecked. Regression tests and native checks used separate ports after an external Jellyfin instance became active; that instance was preserved.

Only the setup executable, the single-executable application update ZIP and their checksum file are public binary assets. Engine binaries are obtained from upstream during installation. Application updates retain the installed engines and runtime data.
