# Releasing

The Marketplace lists this action as **pytest-html-reporter** — that name comes
from `name:` in `action.yml`, not from the repository, and the listing lives at
`github.com/marketplace/actions/pytest-html-reporter`. Leave it alone: renaming
it breaks the listing URL and every link to it. `tests/test_action_yml.py`
fails the build if it changes.

1. Update `CHANGELOG.md`.
2. Tag and push:

   ```console
   $ git tag -a v1.2.3 -m "v1.2.3"
   $ git push origin v1.2.3
   ```

3. Draft a release from the tag on GitHub, and tick **Publish this Action to
   the GitHub Marketplace**.
4. Move the major tag. `.github/workflows/release.yml` does this on publish, so
   normally there is nothing to do — but by hand it is:

   ```console
   $ git tag -f v1 v1.2.3
   $ git push origin v1 --force
   ```

The major tag is the one nearly everybody uses. A release that does not move it
ships to nobody, which is the most common way an action goes stale.
