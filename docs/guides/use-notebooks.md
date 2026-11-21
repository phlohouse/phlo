# Use notebooks in Observatory

Observatory notebooks mix SQL cells, which run on Trino through the Phlo API,
with Python cells, which run in Jupyter kernels. Kernels run in a separate
`phlo-notebook-kernels` service, never inside the API. Notebooks are files in
`notebooks/`, so they are reviewed and versioned with the rest of the project.

Notebooks are a preview feature. They are off until you add them.

## Turn notebooks on

Add notebooks to the project's stack. In a project that already runs Observatory:

```bash
phlo services add notebooks
```

When you set up a new project, choose the profile at the start instead:

```bash
phlo services init --profile notebooks
phlo services start
```

`phlo services init` records the profile's services under `services.enabled` in
`phlo.yaml`, so every later `phlo services start` runs the kernel host too.
Then open **Notebooks** in Observatory. There is nothing else to set.

Adding notebooks:

- adds the `phlo-notebook-kernels` service and builds the API with notebook support;
- turns on kernels, notebook packages and schedules, and records that the
  generated API runs as one process, which kernels need;
- writes two generated secrets to `.phlo/secrets/.env`: the token the API uses
  to reach the kernel host, and the key that signs schedules;
- with the local development sign-in from `--observatory-dev`, lets `dev_user`
  edit, run and query notebooks in each configured environment.

To keep a part off, set it to `0` in the `env:` block of `phlo.yaml`, for
example `PHLO_NOTEBOOK_PACKAGES: "0"`, and run `phlo services init` again.

Trino SQL cells read through the same read-only `trino-preview` service as the
Query page, which phlo adds with the API, so they work without setup in every
environment in `phlo.yaml`. See
[Query page](set-up-observatory-locally.md#query-page).
Python and DuckDB cells need only the kernel host.

With your own authorization setup, the person using notebooks needs
`object.read` and `object.write` on the environment to open and save them,
`run.execute` to start kernels, and `dataset.query` for SQL cells. Scheduling
needs `run.manage` as well, and promoting a cell to a dbt model needs
`asset.manage`.

## How kernels are isolated

Each kernel runs as its own Unix user, from a pool of 64, in a folder only that
user can read. The kernel host applies these rules before it accepts requests:

- A kernel's connection file and key are readable only by its own user, so one
  kernel cannot attach to another.
- Firewall rules reject every new connection a kernel user opens. Kernels cannot
  reach each other, the kernel host's own API, the Phlo API, Trino, MinIO,
  Postgres or the internet. The host checks this at startup and exits if a
  kernel user can connect out.
- The kernel host holds no Phlo secrets. Its only secret is the token the API
  uses to call it.
- The container drops every Linux capability it does not need, cannot gain new
  privileges, has a read-only root filesystem and limits memory and process
  counts.
- The API forwards only `execute`, `complete`, `inspect` and `kernel_info`
  requests to a kernel and signs them with that kernel's key.

SQL cells never run in a kernel. The API runs them on Trino as the signed-in
user, and their results reach the kernel as data frames. A kernel has no
credentials of its own.

## Packages

When packages are allowed, a notebook lists the packages it needs. The kernel
host installs them as a separate installer user into an environment shared by
notebooks with the same package list. Installs accept wheels only, so no
package build code runs. The installer user can reach DNS and HTTPS, plus the
index and proxy you configure. Kernel users still have no network.

Point installs at your own index with `PHLO_NOTEBOOK_PACKAGE_INDEX`, or pass an
`HTTPS_PROXY` to the kernel host. Both are allowed through the firewall.

## Schedules

A schedule runs a notebook unattended as the person who set it. The schedule is
signed over the notebook path, the schedule and the notebook's code. If anyone
else edits a scheduled notebook, the schedule pauses until its owner saves the
notebook or sets the schedule again. A schedule also pauses if its owner loses
`run.execute` or, for notebooks with SQL cells, `dataset.query`.

## Limits

- At most 8 kernels run at once. A kernel stops after 35 minutes idle.
- `PHLO_NOTEBOOK_KERNELS_MEMORY` caps the kernel host container (default `4g`).
- `PHLO_NOTEBOOK_CELL_TIMEOUT` stops a cell after this many seconds (default
  1800). `PHLO_NOTEBOOK_MAX_ROWS` caps SQL results (default 10000).
- Kernels share one container. The isolation above separates users from each
  other and from the platform, but it is not a virtual machine boundary. Do not
  give notebook access to people you would not let run jobs.
- The kernel host runs on one machine. Kernels are not spread across workers.
