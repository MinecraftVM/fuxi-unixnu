# 交接说明（2026-10-24）

这份文档把“已经做到哪、怎么验证的、还差什么”压缩到一页，方便换人或隔一段时间后继续。

## 1. 一句话现状

**固件 + UniXNU 菜单已经在小米 13（fuxi）真机上跑通**；XNU（iOS）内核的构建链路已全部离线验证，
只差在 GitHub Actions（macOS）上真正跑一次；payload 卷的发现已按真机日志定位到
`/chosen` 与 Kernel 窗口，待真机确认。

## 2. 交付物

| 交付物 | 位置 |
| --- | --- |
| 可推送的构建仓库（工作流 + 脚本 + 8 篇中文文档） | `fuxi-unixnu/` |
| 一键构建工作流（Ubuntu 预检 + macOS，uefi/kernel/full 三种模式） | `.github/workflows/build-fuxi.yml` |
| 可 `fastboot boot` 的镜像（3 张推荐 + 历史版本） | `out/flash-kit/`、`out/*.img` |
| 镜像清单与校验值 | `docs/IMAGES.zh.md` |
| 验证过的本地构建步骤 | `docs/BUILD-LOCAL.zh.md` |
| 只编内核 + 本地组装 | `docs/ASSEMBLE-LOCAL.zh.md` |

## 3. 已验证（可复现）

| 项 | 证据 |
| --- | --- |
| IPSW 身份 + kernelcache pin | `scripts/verify-ipsw.py`：`f18b4df…` 与 board.toml 一致 |
| XNU 源码 + 58 个补丁 + 26 个 DriverKit 头 | Linux 实跑：67 文件 +2454/−92；26 头 blob 校验 |
| console 链接的归档生产者与符号面 | 6 个归档逐条核对；13 个 extern 里只有 2 个无条件，由 `libLegacyStub.a` 提供 |
| prelink 的 31 个 root kext | 全部能在 kernelcache 中找到 |
| ADT 生成 | 13,496 字节，chip-id/unique-chip-id/dram 等节点核对 |
| payload 组装（ADT + FAT + config + 打包） | Linux 实跑（panic 补丁需真实内核） |
| boot.img v3 打包 | 逐段回读校验；payload FAT 描述（label/sectors） |
| QEMU 冒烟 | UniXNU + FAT payload 端到端 |
| defconfig 守卫 | `check-defconfig.py`：console 通过、upstream 被拦 |
| 全新 clone | 22 文件、无二进制、链接无失效、py_compile/shellcheck 干净 |

## 4. 还差的两步（都需要外部资源）

### 4.1 内核构建（macOS / Actions）

```
mode   = kernel
config = console     ← 第一次必须
xcode  = 留空
```

预期：6 个归档 → bootstrap 链接报 `_native_legacy_display_probe`（正常）→ 第二次链接 → `prelink`。

失败时：artifact 里有 `logs/`，或直接看 Action 日志里 “Show the failing kernel log” 的尾部。

### 4.2 真机 payload 发现

**已定位的机制**（2026-10-24 真机日志）：

- ABL 把 boot image 的 ramdisk **接在 vendor ramdisk 后面**放进同一个 initrd：
  两次日志的 `initrd size` 相差正好 64 MiB；
- 起始地址 `initrd-start + vendor_size` **不是 512 字节对齐**（例如 `B3FFFBB9`），
  所以所有按 512 B 步长的扫描都会跳过去；
- 因此 `--discover` 在 `[initrd-start, initrd-end)` 内**按字节**搜 `FUXIESP` 标签，
  回推 0x47 校验 BPB；模拟缓冲验证命中 `B3FFFBB9`。

测试镜像与判读：

| 镜像 | 关键输出 | 含义 |
| --- | --- | --- |
| `hello-lz4dec`（2.1 MB） | `lz4 at +...` → `payload at ... (lz4 decompressed)` | ABL 拷贝压缩帧，固件解码成功 |
| 同上 | `no lz4 frame in the initrd range` | 范围内没有压缩帧 |
| `hello-kdtb2` | `fdt at ...` → `payload at ... after the fdt` | kernel 字段尾部被保留 |
| `hello-rawk` | 同上（kernel 不 gzip） | gzip 解压释放原缓冲时的备选 |
| `hello-probe2` | 三个 anchor 的 hexdump | 卷在范围内与否的原始证据 |

如果三条都不行，剩余通道是：**USB mass storage**（FV 里有 UsbBus/UsbMassStorage）或
**分区**（FV 里有 PartitionDxe/FatDxe）；两者都要求 ABL 之后固件能枚举到相应块设备，
尚未在真机验证。

```sh
fastboot boot out/boot-uefi-hello-label64.img
```

日志关注：

```
FUXIRAMDISK: label scan in B22B5000..B7FFEBB9 (5D48BB9 bytes)
FUXIRAMDISK: payload at B3FFFBB9 (label at +...)
```

命中后菜单里出现 **fuxi: UEFI hello (no XNU)**。工作流已固定 `--discover`，CI 产出的
boot.img 带同样的发现逻辑。

## 5. 已知限制

- console 配置的内核**没有屏幕输出**（boot progress 被构建脚本的硬性组合禁用），可见信号止于 DarwinBoot 交接；
- trust cache bundle 的两个 pin 需要设备的 `static-trustcache.bin`，仓库里没有；
- 上游 `uefi-fuxi` 更新很快，跟版本时先跑 `apply-fixes.py` 的兼容路径；
- 仓库保持私有，不分发 Apple 派生的内核/trust cache。

## 6. 继续下去的最短路径

1. push 仓库 → Actions `mode=kernel, config=console`；
2. 下载 `kernel/` → 按 ASSEMBLE-LOCAL 本地组装 `boot.img`；
3. 真机先跑 `boot-uefi-hello-dt.img` 确认 payload；再 `fastboot boot` 带内核的镜像；
4. 看到 DarwinBoot 交接即里程碑；后续接显示/触控/存储驱动再逐项推进。