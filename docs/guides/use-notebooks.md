# Use notebooks in Observatory

Observatory notebooks mix SQL cells, which run on Trino through the Phlo API,
with Python cells, which run in Jupyter kernels. Kernels run in a separate
`phlo-notebook-kernels` service, never inside the API. Notebooks are files in
`notebooks/`, so they are reviewed and versioned with the rest of the project.

Notebooks are a preview feature. Every switch below is off by default.

## Turn notebooks on

1. Add the notebook settings to the `env:` block in `phlo.yaml`:

   ```yaml
   env:
     PHLO_API_EXTRAS: notebooks,trino
     PHLO_NOTEBOOK_KERNELS: "1"
     # Kernels and query sessions live in one API process.
     PHLO_V1_QUERY_SINGLE_PROCESS: "1"
     # Optional: let people add packages to a notebook.
     PHLO_NOTEBOOK_PACKAGES: "1"
     # Optional: let people run notebooks on a schedule.
     PHLO_NOTEBOOK_SCHEDULES: "1"
   ```

   Only `1` turns a switch on.

2. Generate the services configuration with the `api` and `notebooks` profiles:

   ```bash
   phlo services init --profile api --profile notebooks
   ```

   This adds the `phlo-notebook-kernels` service and writes two generated
   secrets to `.phlo/secrets/.env`:

   - `PHLO_NOTEBOOK_GATEWAY_TOKEN`, which the API uses to reach the kernel host.
   - `PHLO_NOTEBOOK_SCHEDULE_KEY`, which signs schedules.

   Both are required. The kernel host refuses to start without a token of at
   least 32 characters, and schedules are refused without the key.

3. Start the stack and open **Notebooks** in Observatory:

   ```bash
   phlo services start --profile api --profile notebooks --build
   ```

The person using notebooks needs `object.read` and `object.write` on the
environment to open and save them, `run.execute` to start kernels, and
`dataset.query` for SQL cells. Scheduling needs `run.manage` as well.

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
