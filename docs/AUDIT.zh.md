# 交付自检（2026-10-24）

## 1. 结论

构建链路、固件镜像、打包工具和中文文档都已交付，并且每一环都有可复现的验证。
真机上 **固件 + UniXNU 菜单**已经跑通；**XNU 内核**只差一次 macOS Actions 构建；
**payload 卷发现**正在收敛（真机日志已把位置问题缩小到 ABL 的 initrd 布局）。

## 2. 交付物

| 交付物 | 位置 |
| --- | --- |
| 构建仓库（47+ 提交） | `fuxi-unixnu/` |
| 工作流（预检 + macOS，uefi/kernel/full） | `.github/workflows/build-fuxi.yml` |
| 10 个脚本 | `scripts/` |
| 10 篇中文文档 | `docs/` |
| 可刷镜像 | `out/flash-kit/`、`out/boot-uefi-*.img` |

## 3. 验证矩阵

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 工作流语法 | `actionlint .github/workflows/build-fuxi.yml` | rc=0 |
| Python 脚本 | `python3 -m py_compile scripts/*.py` | 全部通过 |
| Shell 脚本 | `shellcheck -S warning scripts/*.sh` | 干净 |
| 固件补丁标记 | `check-firmware.py --uefi uefi --device device` | rc=0（未打补丁时 rc=1） |
| console 配置守卫 | `check-defconfig.py --device device --config fuxi_console_defconfig` | rc=0 |
| IPSW kernelcache pin | `verify-ipsw.py --unixnu UniXNU --device device --lzfse ...` | `f18b4df…` 一致 |
| XNU 补丁序列 | 58/58 应用（67 文件，+2454/−92） | ✅ |
| DriverKit 头 | 26 个，blob 哈希校验 | ✅ |
| root kext | 31 个都在 kernelcache | ✅ |
| ADT / payload / boot.img | 本地实跑 + 逐段回读 | ✅ |
| 真实内核尺寸预算 | 46 MB kernelcache → 64 MiB payload 卷 | ✅ |

## 4. 未完成项（都需要外部执行）

1. **真机 payload 发现**：`out/boot-uefi-hello-probe2.img` 的三个 anchor hexdump；
2. **XNU 内核构建**：Actions `mode=kernel, config=console`，成功后本地按
   [ASSEMBLE-LOCAL.zh.md](ASSEMBLE-LOCAL.zh.md) 组装。

## 5. 已知限制

- console 配置的内核没有屏幕输出（构建脚本硬性要求，见 FULL-MODE 第 8 节）；
- trust cache bundle 的两个 pin 需要设备的 `static-trustcache.bin`；
- 真机无 UART，一切以屏幕输出为准；
- 仓库保持私有，不分发 Apple 派生的内核/trust cache。
