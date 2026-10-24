# fuxi 移植现状（2026-10-24）

一句话：**固件和 UniXNU 界面已经在小米 13 上跑起来了**；payload 卷的发现正在收敛；
XNU 内核只剩 macOS 编译这一步，其余环节都已在 Linux 上验证过。

## 1. 真机已经到哪一步

| 阶段 | 状态 | 证据 |
| --- | --- | --- |
| ABL 接受 v3 boot.img | ✅ | `fastboot boot` 正常 |
| BootShim → UEFI 固件 | ✅ | 屏幕出现 DXE dispatcher 日志 |
| DEBUG 上屏（FrameBufferSerialPortLib） | ✅ | `SEC: ...`、`Loading driver ...` 等 |
| DXE 驱动枚举 | ✅ | 55 个内存描述符、FuxiKeyInputDxe/Smbios/UniXNUDxe 依次加载 |
| UniXNU 应用程序 | ✅ | 菜单显示，含 Restart / Power off |
| payload 卷发现 | ⏳ | 上游 `a7f42bfc` 新增“提示下方 128 MiB”有界扫描；新镜像再用 `/chosen` + Kernel 窗口兜底 |
| XNU 内核 | ⏳ | 需要 macOS 编译 |

## 2. 真机内存布局（从屏幕日志读到）

```
FUXIDT: entry x0=0x…（合法 FDT，/chosen 有 linux,initrd-start/end，len=8）
FUXIMAP: A8000000 256 MiB type 0   ← 平台 Kernel 窗口（payload 最可能在这里）
FUXIMAP: BAB00000 186 MiB type 7   ← DXE heap
FUXIMAP: 89800000 402 MiB type 0
FUXIMAP: D8800000 138 MiB type 0
FUXIRAMDISK: no volume; hob=1 base=0x…C4C2914   ← SEC 提示在低内存，扫描方向/窗口不对
```

结论：**用 `/chosen` 的 initrd 地址，或直接扫 `0xA8000000` 那 256 MiB**。
`boot-uefi-hello-full.img` 就是这么做的（还带 ±4 MiB 容错）。

## 3. 完整链路与验证状态

| 环节 | 状态 | 备注 |
| --- | --- | --- |
| UEFI 固件（Mu-Silicium fuxiPkg） | ✅ 真机 | 上游 `c15fe352` 起自带 reporting ramdisk 驱动 |
| boot.img v3 打包（BootShim+gzip+DTB+ramdisk） | ✅ 本地实测 | `pack-bootimg-v3.py`，逐段回读 |
| UniXNU.efi + 菜单 + payload 加载 | ✅ QEMU + 真机 | QEMU 里跑过真实 FAT 镜像 |
| payload 卷发现 | ⏳ 真机待确认 | 新镜像带 `/chosen` + Kernel 窗口扫描 |
| ADT 生成 | ✅ Linux 实测 | 13,496 字节，节点/身份值核对 |
| payload 组装（ADT+FAT+config） | ✅ Linux 实测 | 用真实 kernelcache 跑通（panic 补丁除外） |
| XNU 源码 + 58 个补丁 | ✅ Linux 实测 | 67 文件，+2454/−92 |
| DriverKit 头恢复 | ✅ Linux 实测 | 26 个头，blob 哈希校验 |
| IPSW 身份 + kernelcache pin | ✅ Linux 实测 | `f18b4df…` 与 board.toml 一致 |
| prelink 的 31 个 root kext | ✅ Linux 实测 | 都在 kernelcache 的字符串里 |
| **XNU 编译/链接** | ⏳ **只剩 macOS** | workflow `mode=full, config=console` |
| CI 预检（配置 + IPSW pin） | ✅ 本机按 CI 路径实测 | 预检失败不消耗 macOS 分钟 |
| CI 首次真机运行 | ⚠️ config=upstream 触发 BOOT_PROGRESS 守卫 | 默认已改为 console，预检会先拦 |
| trust cache bundle | ⏳ 需要设备输入 | `static-trustcache.bin` |

## 4. 镜像清单

| 镜像 | 用途 |
| --- | --- |
| `boot-uefi-only.img` | 最早期固件（会卡在旧 ramdisk 驱动的死循环） |
| `boot-uefi-diskdbg.img` | 不 halt 的固件，已验证能进 UniXNU 界面 |
| `boot-uefi-diskup.img` | 同上 + 向上 16 MiB 扫描 |
| `boot-uefi-debugcon.img` | DebugLib 改走 ConOut（一般不需要，DEBUG 本来就上屏） |
| `boot-uefi-probe.img` | FUXIPROBE 驱动：GOP 状态 + 内存扫描后停机 |
| `boot-uefi-diag.img` | hello payload + FUXIRAMDISK/FUXIDT/FUXIMAP 三组日志 |
| `boot-uefi-hello-down/up/new.img` | 带 hello payload 的三种扫描策略 |
| **`boot-uefi-hello-full.img`** | **/chosen ±4 MiB + Kernel 窗口 + 常规内存** |

## 5. 仓库里的工具

| 脚本 | 作用 |
| --- | --- |
| `apply-fixes.py` | 构建修正（默认删重复 splash；`--console/--probe/--debug-conout/--ramdisk-debug/--ramdisk-scan/--memmap/--dtchosen/--ramdisk-full`） |
| `pack-bootimg-v3.py` | shim+FD+DTB+payload → v3 boot.img，含 FAT 描述与逐段校验 |
| `qemu-smoke.sh` | QEMU/AAVMF 上验证 UniXNU + payload（目录 ESP 或真实镜像） |
| `verify-ipsw.py` | Linux 上核对 IPSW 身份与 kernelcache pin |
| `gpt-userdata.py` / `blob-scan.py` | 从设备转储读 userdata LBA / 固件窗口 |
| `diagnose-logs.py` | macOS 构建失败日志归类 |
| `check-defconfig.py` | 在 macOS 之前检查 defconfig 是否违反构建脚本的硬性组合 |
| `make-test-payload.py` | 生成 hello payload FAT 卷 |

## 6. 下一步

1. 真机确认 `boot-uefi-hello-full.img` 的菜单里出现 **fuxi: UEFI hello (no XNU)**；
2. 若命中：把 payload 发现逻辑固定进 board/驱动，然后跑 GitHub Actions
   `mode=full, config=console`，产出的 `boot.img` 里就是真正的 XNU 内核；
3. 若未命中：把新的 `FUXIRAMDISK:` 行（`chosen initrd-start=` 的值）发回来，按地址改；
4. 内核能进 panic 屏之后，再考虑显示（GOP 已在 UniXNU 阶段工作）、触控（Goodix GT9916）、存储（UFS/PMGR）。

## 7. 已知风险

- XNU 编译从未在 macOS 上跑过；console 配置的静态闭环已验证，但链接仍可能缺符号；
- 上游仍在高频推进（`uefi-fuxi` 一天多个提交），跟版本时注意 `apply-fixes` 的兼容处理；
- trust cache / Data seed 是设备输入，仓库里没有；
- 真机一切以屏幕为准（没有可接触的 UART）。