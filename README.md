# 小米 13（fuxi）跑 UniXNU：构建与部署

把 [UniXNU](https://github.com/UniXNU/UniXNU)（在非 Apple ARM64 硬件上通过 UEFI 跑 Darwin/XNU）
搬到小米 13（fuxi，SM8550）。这个仓库不重复写驱动，它把**上游已有的 fuxi 半成品**固定版本、
修掉构建链上会让你卡住的坑（重复的 splash 节点、v3 打包、FD 布局说明），
然后用一条 macOS Actions 流水线把 **boot.img** 做出来。

## 现在的真实进度

社区里已经有人在推进 fuxi 移植，两个仓库是本项目的上游：

| 仓库 | 内容 | 状态（2026-10-24） |
| --- | --- | --- |
| [hanzi123-bilp/uefi-fuxi](https://github.com/hanzi123-bilp/uefi-fuxi)（分支 `fuxi`） | Mu-Silicium 的 fuxi UEFI 移植、DTB、BootShim、v3 打包器 | 仍在 bring-up：正在调 payload 定位/SEC 传参 |
| [hanzi123-bilp/unixnu-fuxi](https://github.com/hanzi123-bilp/unixnu-fuxi) | UniXNU 设备仓库：board.toml、defconfig、ADT 脚本、stub | **stage 2a**：只求链接成功并进 XNU 控制台；**没有**存储、显示、触控驱动 |

也就是说：**“移植到能进系统桌面”还差很远**（要写 fuxi 的显示、触控、UFS/PMGR 驱动），
但“把 UniXNU 跑到 XNU 内核入口/控制台”是现在这一版的目标，也是本仓库交付的内容。

这个仓库负责：

1. **修掉构建链上的坑**（见“构建时的修正”）：默认只删掉重复的 splash 节点，不擅自改上游实测过的固件布局；
2. **一条能跑完整条链的 macOS Actions 构建**：内核 → UniXNU → 固件 → payload → boot.img；
3. 用 fuxi 自己的 v3 打包器生成**原厂 ABL 能接受的 boot 镜像**（`unixnu bootimg` 只能出 v0）；
4. 中文的部署、验证、排障、回滚步骤（[`docs/DEPLOY.zh.md`](docs/DEPLOY.zh.md)）。

## 验证到哪一步了

| 验证项 | 状态 | 证据 |
| --- | --- | --- |
| Linux 上编出 UniXNU.efi 和 fuxi 固件卷 | ✅ 实测 | `docs/BUILD-LOCAL.zh.md` 第 6 节 |
| boot.img 结构（v3、BootShim、gzip、DTB、ramdisk） | ✅ 实测 | `boot.json` 逐段回读 |
| QEMU/AAVMF 上跑 UniXNU 菜单并启动 EFI payload | ✅ 实测 | `docs/BUILD-LOCAL.zh.md` 第 5 节 + 截图 |
| 真实的 192 MiB payload FAT 镜像能被发现/启动 | ✅ 实测 | `scripts/qemu-smoke.sh --payload-image` |
| UniXNU 界面在小米 13 上显示 | ✅ 真机反馈（2026-10-24） | "能进unixnu界面" |
| payload 的 ADT 生成与结构 | ✅ 实测 | 13496 字节，节点与 chip-id/unique-chip-id 已核对 |
| IPSW 身份 + kernelcache sha256 固定值 | ✅ 实测 | `scripts/verify-ipsw.py`：`f18b4df…` 与 board.toml 一致 |
| trust cache bundle 的两个 pin | ⏳ 需要设备 `static-trustcache.bin` | 原始成员哈希不等于 bundle 哈希 |
| 构建修正脚本在全新 clone 上可用 | ✅ 实测 | 新 clone + `apply-fixes.py --console/--probe/--ramdisk-full` 全部通过 |
| XNU 补丁序列 + DriverKit 头恢复 | ✅ Linux 实测 | 58/58 补丁，67 文件；26 个头，blob 哈希校验 |
| prelink 的 31 个 root kext | ✅ Linux 实测 | 全部能在 kernelcache 里找到 |
| fuxi 真机固件 | ⏳ 待你测 | `boot-uefi-only.img` |
| macOS 上编 XNU 内核 | ⏳ 待 Actions | workflow `mode=full` |

## 两种构建模式

| 模式 | 产物 | 用途 | 需要 Apple 输入 |
| --- | --- | --- | --- |
| `uefi` | 只有 UEFI 固件 + UniXNU 菜单的 boot.img | 先确认固件能启动、屏幕能亮、菜单能进 | 否 |
| `kernel` | 只编 XNU 并上传 `kernel.macho` | 交给本地组装（见 [ASSEMBLE-LOCAL.zh.md](docs/ASSEMBLE-LOCAL.zh.md)），省 macOS 分钟 | 是 |
| `full` | 固件 + XNU 内核 + ADT + payload | 真正进 XNU 控制台 | 是（iPadOS 17.7.11 的 kernelcache/trust cache） |

建议先用 `uefi` 模式验证硬件通路，再跑 `full`。

## 快速开始（GitHub Actions）

1. 在 GitHub 建一个**私有**仓库，把本目录推上去（`git init && git add . && git commit && git push`）。
2. 仓库 → Actions → **fuxi build** → Run workflow：

   | 输入 | 建议 |
   | --- | --- |
   | mode | 第一次选 `uefi`，通过后再选 `full` |
   | config | `upstream`（默认）或 `console`（实验：绕开需要 prelink 候选的归档） |
   | chip_id | `full` 必填，16 位十六进制：`openssl rand -hex 8` |
   | 其他 | 保持默认（上游按 commit 固定） |

3. 工作流会先跑一个 **Ubuntu 预检**（配置修正 + IPSW kernelcache pin 校验 + defconfig 选择），
   预检失败不会消耗 macOS runner 的分钟；通过后才进入 macOS 构建。
4. 跑完在 Artifacts 下载 `fuxi-*-boot-*`，里面有 `boot.img`、`boot.json` 和日志。
5. 手机（已解锁 BL）：

   ```sh
   adb reboot bootloader
   fastboot devices          # 应看到设备
   fastboot boot boot.img    # 只启动，不写入分区
   ```

   观察屏幕；要回到 Android：长按电源强制重启，或 `fastboot reboot`。

> **注意**：`full` 模式的 artifact 里有从 iPadOS 恢复镜像提取的内核，请保持仓库私有。
> 免费额度下 macOS runner 按 10 倍计费，一次完整构建大约 40–70 分钟，先跑 `uefi` 省额度。

## 构建时的修正

`scripts/apply-fixes.py` 会在构建前处理两处（幂等、打印 diff）：

### 1. 去掉重复的 splash 节点：`device/board.toml`

board.toml 会给 boot 镜像的 DTB 追加：

```
/reserved-memory/splash_region  reg = <0x0 0xb8000000 0x0 0x2b00000>
```

但 `uefi/Resources/DTBs/fuxi.dtb` **本来就有这个节点**（本仓库解码确认，值完全相同）。
`fdtput -c` 不能创建已存在的节点，会直接失败；本仓库的 v3 打包器也不改 DTB。
所以把它删掉。

### 2. 固件卷布局：**默认不动**

`Resources/Configs/fuxi.toml` 的注释还写着“`0xA7000000, 0x00400000`，不要抄 sheng 的
`0xA7100000/0x300000`”，但表里的值确实是 `0xA7100000/0x300000`。这**不是笔误**：
上游提交 `ad576f83`（“use the 3 MiB FD layout every working device uses”）把它改成了
1 MiB 保留 + 3 MiB FD，并同步改了 `fuxi.fdf` 和 MemoryMapLib；pipa/nabu/marble 都是这个布局。

所以默认**保持上游值**。只有要在真机上做 A/B 时才用 workflow 的 `fd_base`/`fd_size`
输入显式覆盖；镜像里实际用的值会写进 `boot.json` 并在构建日志里打印。

## boot.img 的格式

```
0x0000  Android boot image header v3（1580 字节，补齐到 4096）
0x1000  gzip(BootShim + UEFI 固件卷) + fuxi.dtb   ← kernel 字段
...     UniXNU payload FAT 卷                    ← ramdisk 字段
```

原厂 ABL 接受 v3、拒绝 v0，所以不用 `unixnu bootimg`，改用
`scripts/pack-bootimg-v3.py` + 上游的 `tools/mkbootimg_v3.py` 打包并逐段回读校验。

## 目录

```
.github/workflows/build-fuxi.yml   Ubuntu 预检 + macOS 一键构建
scripts/apply-fixes.py             配置修复（幂等；默认只删重复 splash 节点）
scripts/pack-bootimg-v3.py         BootShim + 固件 + DTB + payload → boot.img（v3，含校验）
docs/DEPLOY.zh.md                  部署、验证、排障、回滚
docs/STATUS.zh.md                  移植进度、还缺什么、下一步怎么做
docs/BUILD-LOCAL.zh.md             无 Mac / 无 GitHub 时在 Linux 本地只编固件（实测步骤）
docs/ASSEMBLE-LOCAL.zh.md          只在云端编内核，payload 和 boot.img 在本地组装
docs/DEVICE-TEST.zh.md             上机测试协议、数据采集、反馈模板
docs/FULL-MODE.zh.md               full 模式已知缺口：静态库链与 prelink 候选
scripts/gpt-userdata.py            从 GPT 转储读 userdata 的 LBA（board.toml 用）
scripts/blob-scan.py               从 xbl_config 转储核对固件卷窗口（[uefi_fd]）
scripts/diagnose-logs.py           失败日志归类（library not found / prelink / 工具缺失…）
scripts/qemu-smoke.sh              QEMU/AAVMF 冒烟测试（已验证 UniXNU + payload 链路）
scripts/verify-ipsw.py             Linux 上核对 IPSW 身份与 kernelcache pin（自建 LZFSE 解码）
```

## 上游版本固定

工作流默认固定到写这套流程时验证过的 commit：

| 仓库 | ref |
| --- | --- |
| UniXNU/UniXNU | `1a01c2afa197d975d04da892325c56b6b14aaecb` |
| hanzi123-bilp/unixnu-fuxi | `d12c0585c7c82941c17e4af6bace12dc46953c85` |
| hanzi123-bilp/uefi-fuxi | `a7f42bfc27dc33fa2ebf3c99752f2cd75a1def34` |

上游还在高频推进；要跟新版在 Run workflow 里改 `*_ref` 即可（可能会和补丁冲突，日志里能看到）。

## 许可

本仓库的脚本/文档：BSD-2-Clause-Patent。上游 UniXNU、Mu-Silicium、XNU 各自遵循其许可证；
Apple 的 IPSW 内容不随本仓库分发。