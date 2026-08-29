# Third-Party Notices

## dbt-labs/jaffle_shop_duckdb

- Project: `dbt-labs/jaffle_shop_duckdb`
- URL: <https://github.com/dbt-labs/jaffle_shop_duckdb>
- Fixed branch: `duckdb`
- Fixed commit: `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`
- License: Apache-2.0
- Submodule path: `third_party/jaffle_shop`
- Reused content: seeds, core models, and related schema tests.

The repository is included as a Git submodule. All PostgreSQL configuration is
kept outside the submodule; no upstream source files have been modified.
Initialize the dependency with:

```powershell
git submodule update --init --recursive
```

The complete Apache License 2.0 text is provided in the project-level
`LICENSE` file and is copied from the pinned submodule license.
