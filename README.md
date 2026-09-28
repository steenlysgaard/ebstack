# ebstack

`ebstack` is a small command-line wrapper around EasyBuild for maintaining and
installing a repeatable stack of modules across HPC partitions.

```bash
export EBSTACK_CONFIG=./ebstack.yaml
export CPU_ARCH=skylake_el9

ebstack show-config
ebstack dry-run
ebstack fetch-sources
ebstack install
ebstack check-logs --arch skylake_el9 --since 5d
```

You can also pass the config explicitly:

```bash
CPU_ARCH=skylake_el9 ebstack --config ./ebstack.yaml show-config
```

## Why This Exists

When a new HPC partition is deployed, the software stack usually needs to be
rebuilt from a known list of EasyBuild easyconfigs. That list often differs by
CPU generation, CPU vendor, operating system, partition policy, and whether the
partition has GPUs.

`ebstack` lets you keep that intent in one YAML file:

- which modules should be installed everywhere
- which modules should be installed only on Intel, AMD, or GPU partitions
- which EasyBuild options belong to those module groups
- which Slurm defaults should be used for a given CPU architecture
- where EasyBuild job logs should be written

The main use case is tracking the modules you want installed on new HPC
partitions, then installing the full selected list with a short command sequence.
For example, one partition can use an Intel CPU architecture and a CPU-only
stack, while another can use an AMD GPU stack with CUDA compute capabilities and
additional GPU-only modules.

## Typical Workflow

### 1. Build The Module And Partition List

Create an `ebstack.yaml` file that describes the partitions you care about and
the EasyBuild easyconfigs to install.

```yaml
defaults:
  paths:
    log_root: logs
  jobs:
    partition: build
    account: hpc-support
    job_cores: 16
    job_max_walltime: 12

architectures:
  skylake_el9:
    cpu_vendor: intel
    stack: cpu
    jobs:
      partition: skylake-build

  zen4_gpu_el9:
    cpu_vendor: amd
    stack: gpu
    cuda_compute_capabilities: ["8.0", "9.0"]
    jobs:
      partition: gpu-build
      gres: gpu:1
      mem: 128G

layers:
  common:
    easyconfigs:
      - zlib-1.3-GCCcore-13.2.0.eb
      - CMake-3.27.6-GCCcore-13.2.0.eb

  intel:
    easyconfigs:
      - impi-2021.10.0-intel-compilers-2023.2.1.eb

  amd:
    easyconfigs:
      - OpenMPI-4.1.6-GCC-13.2.0.eb

  gpu:
    easyconfigs:
      - CUDA-12.2.0.eb
    options:
      - --accept-eula-for=CUDA
```

Select the target architecture with `CPU_ARCH`. The value must match an entry
under `architectures`.

```bash
export EBSTACK_CONFIG=./ebstack.yaml
export CPU_ARCH=zen4_gpu_el9
```

### 2. Inspect The Resolved Stack

```bash
ebstack show-config
```

This prints the selected config path, log root, CPU architecture, selected
layers, easyconfig count, EasyBuild options, Slurm environment, and the EasyBuild
command that would be used for an install.

Use `--only` to inspect a subset:

```bash
ebstack show-config --only amd
ebstack show-config --only gpu
```

`--only` accepts `intel`, `amd`, or `gpu`. Without `--only`, `ebstack` selects
`common` plus the CPU vendor layer, and also `gpu` when the architecture is a
GPU stack.

### 3. Run An EasyBuild Dry Run

```bash
ebstack dry-run
```

This runs EasyBuild with the selected easyconfigs and `--dry-run`.

### 4. Fetch Sources

```bash
ebstack fetch-sources
```

`fetch-sources` first asks EasyBuild which modules are missing, then runs
EasyBuild with `--fetch-all` for the missing easyconfigs and dependencies.

### 5. Submit The Install Jobs

```bash
ebstack install
```

`install` runs EasyBuild with:

- `--job`
- `--job-backend=Slurm`
- `--job-deps-type=abort_on_error`
- `--job-output-dir=<log_root>/<CPU_ARCH>/<timestamp>`

Slurm options from the YAML file and CLI overrides are exported as `SBATCH_*`
environment variables for the EasyBuild Slurm backend.

### 6. Check The Logs

```bash
ebstack check-logs
ebstack check-logs --arch zen4_gpu_el9
ebstack check-logs --since 5d
ebstack check-logs --since "2026-09-14 12:00" --show-success
```

By default, `check-logs` prints failed and unknown builds. It exits with status
`1` if any failed or unknown logs are found. Use `--show-success` to include
successful builds in the report.

### Other Useful Commands

```bash
ebstack missing
```

Prints the EasyBuild dry-run lines for modules that are not already installed.

```bash
ebstack local
```

Runs EasyBuild locally without submitting Slurm jobs.

## Installation

Install from the repository with `uv`:

```bash
git clone <repo-url> ebstack
cd ebstack
uv sync
uv run ebstack --help
```

If you use `direnv`, allow the repository environment:

```bash
direnv allow
```

For an editable developer install:

```bash
uv pip install -e .
```

For a user-level command install from a checkout:

```bash
uv tool install .
```

Runtime requirements:

- Python 3.11 or newer
- EasyBuild available as `eb`
- Slurm for `install`
- `git` if you use EasyBuild PR overlays through `--from-pr=<PR>` options

## Configuration Selection

Every invocation requires a config file. Provide it either as a global option:

```bash
ebstack --config ./ebstack.yaml show-config
```

or through the environment:

```bash
export EBSTACK_CONFIG=./ebstack.yaml
ebstack show-config
```

Most stack commands also require `CPU_ARCH`:

```bash
export CPU_ARCH=skylake_el9
```

`check-logs` reads the config for `log_root`, but does not require `CPU_ARCH`.

## Subcommands

### `show-config`

```bash
ebstack show-config [OPTIONS]
```

Resolves the selected stack and prints the EasyBuild command that would be used
for a Slurm install.

Options:

- `--materialize-prs`: fetch EasyBuild PR overlays before printing.
- `--only intel|amd|gpu`: restrict selection to `common` plus one specific layer.
- `--partition TEXT`: override Slurm partition.
- `--gres TEXT`: override Slurm GRES.
- `--account TEXT`: override Slurm account.
- `--qos TEXT`: override Slurm QOS.
- `--mem TEXT`: override Slurm memory per node.
- `--mem-per-cpu TEXT`: override Slurm memory per CPU.
- `--mem-per-gpu TEXT`: override Slurm memory per GPU.
- `--sbatch NAME=VALUE`: set an arbitrary Slurm environment override. May be repeated.
- `--job-cores INT`: override EasyBuild job cores.
- `--job-max-walltime INT`: override EasyBuild max job walltime in hours.

### `dry-run`

```bash
ebstack dry-run [--only intel|amd|gpu]
```

Runs the resolved EasyBuild command with `--dry-run`.

### `missing`

```bash
ebstack missing [--only intel|amd|gpu]
```

Runs a module-aware EasyBuild dry run and prints missing entries.

### `fetch-sources`

```bash
ebstack fetch-sources [--only intel|amd|gpu]
```

Finds missing easyconfigs and dependencies, then fetches their sources with
EasyBuild `--fetch-all`.

### `install`

```bash
ebstack install [OPTIONS]
```

Submits the selected EasyBuild stack as Slurm jobs.

Options:

- `--only intel|amd|gpu`
- `--partition TEXT`
- `--gres TEXT`
- `--account TEXT`
- `--qos TEXT`
- `--mem TEXT`
- `--mem-per-cpu TEXT`
- `--mem-per-gpu TEXT`
- `--sbatch NAME=VALUE`
- `--job-cores INT`
- `--job-max-walltime INT`

### `local`

```bash
ebstack local [--only intel|amd|gpu]
```

Runs the selected EasyBuild command locally, without Slurm job submission.

### `check-logs`

```bash
ebstack check-logs [--since DATE|Nd] [--arch CPU_ARCH] [--show-success]
```

Summarizes `.out` and `.log` files under `log_root`.

Options:

- `--since DATE|Nd`: only check logs modified since an absolute date or relative day count.
- `--arch CPU_ARCH`: only check logs under `log_root/CPU_ARCH`.
- `--show-success`: include successful builds in the printed report.

Accepted `--since` examples:

- `2026-09-01`
- `2026-09-14 12:00`
- `2026-09-14T12:00:00`
- `5d`

## YAML Configuration

The config file has three top-level sections:

```yaml
defaults:
  paths:
    log_root: logs
  jobs:
    partition: build
    account: project-account
    qos: normal
    mem: 64G
    mem_per_cpu: 4G
    mem_per_gpu: 32G
    gres: gpu:1
    job_cores: 16
    job_max_walltime: 12

architectures:
  <CPU_ARCH>:
    cpu_vendor: intel
    stack: cpu
    cuda_compute_capabilities: []
    jobs:
      partition: architecture-specific-partition

layers:
  common:
    easyconfigs:
      - Example-1.0-GCCcore-13.2.0.eb
    options:
      - --accept-eula-for=Example

  intel:
    easyconfigs: []
    options: []

  amd:
    easyconfigs: []
    options: []

  gpu:
    easyconfigs: []
    options: []
```

### Top-Level Sections

- `defaults`: optional defaults shared by all architectures.
- `architectures`: mapping from `CPU_ARCH` values to architecture metadata.
- `layers`: module and option lists for `common`, `intel`, `amd`, and `gpu`.

Unknown top-level sections are rejected.

### `defaults.paths`

Supported keys:

- `log_root`: where install job logs are written and where `check-logs` looks.

Relative paths are resolved relative to the config file directory. If omitted,
`log_root` defaults to `logs`.

`EBSTACK_LOG_ROOT` overrides `defaults.paths.log_root`.

### `defaults.jobs` And Architecture `jobs`

Supported job keys:

- `partition`
- `gres`
- `account`
- `qos`
- `mem`
- `mem_per_cpu`
- `mem_per_gpu`
- `job_cores`
- `job_max_walltime`

`defaults.jobs` applies to every architecture. An architecture-level `jobs`
mapping overrides defaults for that architecture.

Slurm-related values are exported as:

- `partition` -> `SBATCH_PARTITION`
- `gres` -> `SBATCH_GRES`
- `account` -> `SBATCH_ACCOUNT`
- `qos` -> `SBATCH_QOS`
- `mem` -> `SBATCH_MEM_PER_NODE`
- `mem_per_cpu` -> `SBATCH_MEM_PER_CPU`
- `mem_per_gpu` -> `SBATCH_MEM_PER_GPU`

EasyBuild job values become:

- `job_cores` -> `--job-cores=<value>`
- `job_max_walltime` -> `--job-max-walltime=<value>`

The CLI refuses Slurm variables controlled by EasyBuild's Slurm backend, such as
job name, output, dependency, nodes, tasks, and export handling.

### `architectures`

Each architecture entry must contain:

- `cpu_vendor`: `intel` or `amd`
- `stack`: `cpu` or `gpu`

For GPU stacks, `cuda_compute_capabilities` must be non-empty. For CPU stacks,
it must be omitted or empty.

Example:

```yaml
architectures:
  zen4_gpu_el9:
    cpu_vendor: amd
    stack: gpu
    cuda_compute_capabilities: ["8.0", "9.0"]
    jobs:
      partition: gpu-build
      gres: gpu:1
```

### `layers`

Valid layer names:

- `common`
- `intel`
- `amd`
- `gpu`

Each layer can contain:

- `easyconfigs`: a list of EasyBuild easyconfig paths or names.
- `options`: a list of EasyBuild command-line options.

Layer selection:

- Intel CPU stack: `common`, `intel`
- AMD CPU stack: `common`, `amd`
- Intel GPU stack: `common`, `intel`, `gpu`
- AMD GPU stack: `common`, `amd`, `gpu`

`--only intel`, `--only amd`, or `--only gpu` restricts the selected layers for
commands that support it.

### EasyBuild PR Overlays

If a layer option contains `--from-pr=<PR>`, `ebstack` fetches that EasyBuild
easyconfigs pull request, materializes changed files under a cache directory,
and adds them as EasyBuild robot overlays.

The default easyconfigs repository is:

```text
https://github.com/easybuilders/easybuild-easyconfigs.git
```

Override it with:

```bash
export EASYCONFIGS_REPO_URL=<repo-url>
```

### Environment Expansion

Path values support normal environment expansion and defaults of the form:

```yaml
defaults:
  paths:
    log_root: ${EBSTACK_LOG_DIR:-logs}
```

Relative expanded paths are still resolved relative to the config file
directory.
