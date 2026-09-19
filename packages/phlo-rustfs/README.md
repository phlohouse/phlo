# phlo-rustfs

RustFS S3-compatible object storage plugin for Phlo.

## Description

Provides S3-compatible object storage for the data lake using [RustFS](https://github.com/rustfs/rustfs), an Apache 2.0 licensed, Rust-built, 100% S3-compatible object storage server. Stores Iceberg table data, staging files, and backups.

## Installation

```bash
pip install phlo-rustfs
# or
phlo plugin install rustfs
```

## Configuration

| Variable                         | Default       | Description                  |
| -------------------------------- | ------------- | ---------------------------- |
| `RUSTFS_ACCESS_KEY`              | `rustfsadmin` | Access key (username)        |
| `RUSTFS_SECRET_KEY`              | `rustfsadmin` | Secret key (password)        |
| `RUSTFS_API_PORT`                | `9000`        | S3 API port                  |
| `RUSTFS_CONSOLE_PORT`            | `9001`        | Web console port             |
| `RUSTFS_CORS_ALLOWED_ORIGINS`    | `*`           | CORS allowed origins (S3)   |
| `RUSTFS_CONSOLE_CORS_ALLOWED_ORIGINS` | `*`    | CORS allowed origins (console) |

## Usage

```bash
phlo services start --service rustfs
```

This targeted start also prepares `./volumes/rustfs` for the non-root RustFS container and creates
the default `lake`, `warehouse/`, and `stage/` S3 layout automatically.

## Endpoints

- **S3 API**: `http://localhost:9000`
- **Console**: `http://localhost:9001`

## Migration from MinIO

If you're switching from MinIO to RustFS, update your environment variables:

```bash
# Before (MinIO)
AWS_S3_ENDPOINT=http://minio:9000
AWS_ACCESS_KEY_ID=minio
AWS_SECRET_ACCESS_KEY=minio123

# After (RustFS)
AWS_S3_ENDPOINT=http://rustfs:9000
AWS_ACCESS_KEY_ID=rustfsadmin
AWS_SECRET_ACCESS_KEY=rustfsadmin
```


## Entry Points

- `phlo.plugins.services` - Provides `RustfsServicePlugin`, `RustfsSetupServicePlugin`
