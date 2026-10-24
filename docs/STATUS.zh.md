# fuxi 移植现状与路线图

写这份文档时（2026-10-24），fuxi 的 UniXNU 移植由两棵上游仓库支撑，都还在 bring-up 阶段。
本仓库的定位是：**把已有成果固定下来、修掉会直接卡住启动的配置、用可复现的方式产出 boot.img，
并把还缺什么写清楚**。

## 1. 三个部分

| 部分 | 仓库 | 作用 |
| --- | --- | --- |
| 构建驱动 / 通用驱动 / kits | [UniXNU/UniXNU](https://github.com/UniXNU/UniXNU) | `./unixnu` 子命令：取源码、组树、编内核、编固件、做 payload/bootimg |
| 设备仓库 | [hanzi123-bilp/unixnu-fuxi](https://github.com/hanzi123-bilp/unixnu-fuxi) | `board.toml`、`fuxi_defconfig`、ADT 生成器、stub、链接脚本 |
| UEFI 固件 | [hanzi123-bilp/uefi-fuxi](https://github.com/hanzi123-bilp/uefi-fuxi)（`fuxi` 分支） | Mu-Silicium 的 fuxi 移植：`fuxiPkg`、`Resources/Configs/fuxi.toml`、`Resources/DTBs/fuxi.dtb`、v3 打包器 |

上游 UniXNU 还提供 DarwinBoot、NativePrelink、4 KiB XNU 补丁、ArmCpu/ArmDTPlatform 等通用组件。

## 2. 从 fuxi 自己的 DTB 解出来的硬件事实

`uefi/Resources/DTBs/fuxi.dtb` 是这台机器的原厂设备树（qualcomm kalama-mtp）。以下由本仓库
直接解码确认，不是从 sheng 抄的：

| 项 | 值 | 用途 |
| --- | --- | --- |
| SoC | `qcom,kalama-mtp` / `qcom,kalama`，`qcom,msm-id` 含 `0x207` | 对应 `board.toml` 的 `adt.chip_id = "207"` |
| 面板 | `qcom,mdss_dsi_m3_38_0c_0a_dsc_cmd`，1080×2400，DSC command mode | `fuxi.dsc` 的 `PcdFrameBufferWidth/Height = 1080/2400` |
| 触控 | **Goodix GT9916**（`goodix-berlin@5d`，I2C `i2c@a90000`） | **和 sheng 的 Novatek NT36532E 完全不同**，需要新驱动 |
| 触控坐标 | `panel-max-x = 0x438`（1080）、`panel-max-y = 0x960`（2400） | 驱动上报量程 |
| splash | `/reserved-memory/splash_region` = `0xb8000000`, `0x2b00000` | ABL 留在屏幕上的 framebuffer，UEFI GOP 直接接在上面 |
| DRAM | `/memory` 基址 `0x80000000`，`ddr_size = 0xc` | 12 GB 机型；ADT 的 `owned_memory_size` 需要按实际改 |
| 系统版本 | `swinfo.fingerprint=fuxi:13/OS3.0.308.0.WMCCNXM:user` | 读 GPT/分区时确认机型 |

## 3. 上游已经做到哪一步

### UEFI（uefi-fuxi，`fuxi` 分支）

- `fuxiPkg` 有 `.dsc/.fdf/DeviceBuild.py`、MemoryMapLib、ConfigurationMapLib、`FuxiRamDiskDxe`、
  `FuxiKeyInputDxe`、`FuxiBootMemoryDxe`；
- 最近提交在调 **SEC 阶段传 payload 位置**、扫描 RAM 找 FAT 卷、BootShim 保留 x1 等，
  说明固件已经能在真机上跑到“找 payload/找 DTB”这一步；
- `fuxi.dsc` 里仍有 TODO（CPU stack base 沿用了 sheng/nuwa 的 `0xA760D000`、
  `FUXI_VENDOR_HW=0` 不加载 vendor 驱动），这些是后续 bring-up 的已知点。

### 内核（unixnu-fuxi）

- 当前是 **stage 2a：console only**：
  - `fuxi_defconfig` 关掉了 `CONFIG_XNU_NATIVE_UFS`，没有 NativeLegacyDisplay/NativeAdreno；
  - 用 `scripts/build-legacy-stub.py` + `LegacyDisplayStub.c` 把 legacy display 的符号补上；
  - 目标是链接通过、进内核入口、用 panic 屏/启动进度给出反馈；
- `make-fuxi-adt.py` 生成最小 ADT（GICv3、timebase 19.2 MHz、UART/UFS 地址、空 NVRAM proxy）。

## 4. 还缺什么（按 bring-up 顺序）

| # | 目标 | 缺的东西 | 难度 |
| --- | --- | --- | --- |
| 1 | UEFI 起来、有画面、进菜单 | 上游正在做：payload 卷定位、GOP 输出、键位 | 上游进行中 |
| 2 | XNU 到平台专家/控制台 | ADT 校验、memory-map 占位符、panic 屏（本仓库把链路搭好） | 中 |
| 3 | 显示 | 其实**不需要**写面板驱动：SimpleFbDxe 直接复用 ABL 的 splash framebuffer；再做 NativeLegacyDisplay 就能画 boot progress | 低-中 |
| 4 | 触控 | **Goodix GT9916** 的 IOKit 驱动：I2C 读、GPIO 中断/复位、goodix 固件包；sheng 的 NativeNovatekTouch 不能直接复用 | 高 |
| 5 | 存储/UFS | NativeUfs 可复用（同为 SM8550），但要接 PMGR（`sheng-pmgr=2` 那套）、IOStorageFamily prelink 审计 | 高 |
| 6 | 用户态 | trust cache、Data seed、userdata 镜像（macOS 的 `hdiutil`）、每台机器的 LBA | 中-高 |

## 5. 建议的推进顺序

1. **uefi 模式**：先证明固件能在手机上跑起来（本仓库 workflow，inputs: mode=uefi）。
2. **full 模式**：把 XNU 跑到 panic 屏，拍下 panic 文字——它会直接告诉你缺哪个 provider。
3. **显示**：确认 SimpleFbDxe 的 GOP 与 XNU 的 framebuffer 交接（`offline-legacy-*` 参数已经在了）。
4. **触控**：照 `NativeNovatekTouch` 的 IOKit 骨架，写 GT9916 的 I2C 读 + 中断。
5. **UFS**：把 sheng 的 PMGR/owner-root 链搬过来，先只读挂载。
6. **userdata**：最后再做，因为会清数据。

## 6. 这一版的未验证项（诚实清单）

- **没有在真机上端到端验证**：写这份文档的环境没有 macOS、也没有连着的手机；
  内核/载荷/boot.img 的构建逻辑是按上游驱动+官方文档写的，第一次跑 Actions 可能会在
  内核或 EDK2 步骤报错——日志会随 artifact 上传。
- 固件卷布局用的是上游 `ad576f83` 之后的 **0xA7100000 / 0x300000**（1 MiB 保留 + 3 MiB FD，
  和 pipa/nabu/marble 一致）。文件头的旧注释还写着 4 MiB，那是历史遗留，不要照着改。
- ADT 的 `dram_size = 0x80000000` 和 `owned_memory_size = 0x130000000` 沿用 sheng，
  fuxi 是 12 GB 机型，后续要按 `/memory` 的实际值调整。
- `board.toml` 的 `[userdata]` LBA 是 512 GB 版本的；256 GB 版本必须重读 GPT。
- 触控/显示/存储驱动都不在这一版里，**进不了用户态**是预期行为。