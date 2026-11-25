# StreamChrome Server 1.1.1

- Discover now loads additional catalog pages while scrolling, with a Load more button as a fallback. Active filters are retained, overlapping results are deduplicated, and failed pages can be retried without clearing loaded titles. Pagination is enabled only when declared by the addon manifest.
- Carousel controls now use an explicit pause/resume icon. Previous and next navigation work, and automatic rotation resumes after focus leaves.
- Official cached posters are preferred over extracted video frames. Series thumbnail caches no longer prevent official metadata lookup.

Validation: 96 Python tests passed with one Windows symlink privilege skip; all 14 JavaScript suites passed. Independent review found no remaining blockers. The packaged update helper verified replacement, restart, retained local-only ports, preserved user files, and cleanup in a disposable C: installation.

Installer structure was unchanged. A full installation was not repeated for this patch.
