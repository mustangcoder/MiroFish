#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "用法: $0 OLD_VOLUME NEW_VOLUME" >&2
  exit 2
fi

OLD_VOLUME="$1"
NEW_VOLUME="$2"
DOCKER_BIN="${DOCKER_BIN:-docker}"
VOLUME_COPY_IMAGE="${VOLUME_COPY_IMAGE:-alpine:3.20}"
MARKER=".mirofishplus_migration_complete"

if ! "$DOCKER_BIN" volume inspect "$OLD_VOLUME" >/dev/null 2>&1; then
  echo "旧卷不存在，跳过迁移: $OLD_VOLUME"
  exit 0
fi

if "$DOCKER_BIN" volume inspect "$NEW_VOLUME" >/dev/null 2>&1; then
  if "$DOCKER_BIN" run --rm -v "$NEW_VOLUME:/target:ro" "$VOLUME_COPY_IMAGE" \
      sh -c "test -f /target/$MARKER"; then
    echo "目标卷已经完成迁移，直接复用: $NEW_VOLUME"
    exit 0
  fi
  if ! "$DOCKER_BIN" run --rm -v "$NEW_VOLUME:/target:ro" "$VOLUME_COPY_IMAGE" \
      sh -c "test -z \"\$(find /target -mindepth 1 ! -name '$MARKER' -print -quit)\""; then
    echo "错误：目标卷已有未知内容且没有迁移完成标记，拒绝覆盖: $NEW_VOLUME" >&2
    exit 1
  fi
else
  logical_name="${NEW_VOLUME#mirofishplus_}"
  "$DOCKER_BIN" volume create \
    --label "com.docker.compose.project=mirofishplus" \
    --label "com.docker.compose.volume=$logical_name" \
    "$NEW_VOLUME" >/dev/null
fi

echo "正在迁移 Docker 卷: $OLD_VOLUME -> $NEW_VOLUME"
"$DOCKER_BIN" run --rm \
  -v "$OLD_VOLUME:/source:ro" \
  -v "$NEW_VOLUME:/target" \
  "$VOLUME_COPY_IMAGE" sh -ceu '
    source_count=$(find /source -mindepth 1 | wc -l | tr -d " ")
    source_bytes=$(find /source -type f -exec stat -c %s {} \; | awk "{sum += \$1} END {print sum + 0}")
    cp -a /source/. /target/
    target_count=$(find /target -mindepth 1 | wc -l | tr -d " ")
    target_bytes=$(find /target -type f -exec stat -c %s {} \; | awk "{sum += \$1} END {print sum + 0}")
    test "$source_count" = "$target_count"
    test "$source_bytes" = "$target_bytes"
    printf "%s %s\n" "$source_count" "$source_bytes" > /target/.mirofishplus_migration_complete
  '
echo "Docker 卷迁移完成，旧卷已保留: $OLD_VOLUME"
