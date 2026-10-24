# 小米 13（fuxi）部署与排障

本文假设：手机已解锁 bootloader、装了 `adb`/`fastboot`、手上有一份本仓库构建出的 `boot.img`。

> **先读这一段**：现在的 fuxi 移植是 **stage 2a**，目标只是让 XNU 走到内核入口/控制台，
> **不是** iPadOS 桌面。没有显示/触控/存储驱动，进不了用户态。屏幕能显示的是
> UEFI/UniXNU 菜单、启动进度或 panic 屏——这已经是这一步想要的结果。

## 1. 前置条件

### 手机

- 小米 13（fuxi，SM8550），**bootloader 已解锁**。
  - 设置 → 我的设备 → 全部参数 → 连点 MIUI/HyperOS 版本开启开发者选项；
  - 开发者选项 → USB 调试；
  - 解锁走小米社区的解锁工具（需要账号、绑定设备、等 168 小时），解锁会**清空数据**。
- 用原装/可靠数据线连接电脑，`adb devices` 能看到设备。

### 电脑

- Linux/Windows/macOS 均可，只要有 `adb` 和 `fastboot`（Android platform-tools）。
- 不需要本地 Android 源码树；LineageOS 那棵 180 GB 的树跟这件事无关。

### 镜像

从 GitHub Actions 的 artifact 里下载 `boot.img` 和 `boot.json`。

## 2. 先验证固件（uefi 模式）

`uefi` 模式的 boot.img 里只有 Mu-Silicium 固件 + UniXNU 菜单，没有 XNU 内核。
它用来确认三件事：**ABL 能加载镜像、固件能跑起来、屏幕能亮**。

```sh
adb reboot bootloader
fastboot devices
fastboot boot boot.img
```

预期：

- 屏幕出现 UEFI/UniXNU 的输出（可能是花屏、可能停在菜单，取决于 bring-up 进度）；
- 没有立刻黑屏重启；
- 长按电源键可以强制重启回到 Android。

如果这一步就黑屏，先别急着跑 `full` 模式，按第 5 节排查固件。

## 3. 再验证内核（full 模式）

`full` 模式在内核字段里多带一个 gzip(XNU) —— 更准确地说：payload RAM disk 里带着：

```
EFI/UniXNU/unixnu.toml        启动配置（内核参数、菜单项）
EFI/UniXNU/Payloads/kernel.macho
EFI/UniXNU/Payloads/kernel.macho.adt   Apple 设备树
```

启动：

```sh
fastboot boot boot.img
```

预期的可见信号（按 bring-up 阶段）：

1. UEFI 起来，屏幕有输出；
2. UniXNU 菜单出现，能看到 `xnu` 项；
3. 选它之后 DarwinBoot 打印交接信息；
4. XNU 打印版本/启动参数，随后到达平台专家；
5. 因为这一版没有显示驱动，**屏幕上的最后画面通常就是 panic 屏**（`panic_screen = true` 已经
   把 panic 输出补进内核副本）。把屏幕上的文字拍下来，就是最有用的排障材料。

没有外露 UART：小米 13 没有可用的串口调试口，**一切以屏幕输出为准**。这也是为什么
`board.toml` 里保留了 `panic_screen` 和 `boot-progress`。

## 4. 只启动，不写入

本文所有步骤都用 `fastboot boot`，镜像只加载到内存，**不写任何分区**：

- 断电/强制重启即恢复 Android；
- 不会覆盖 `boot`/`init_boot`/`userdata`；
- 不需要 `fastboot flash`。

除非以后要做 userdata（第 7 节），否则不要 `fastboot flash`。

## 5. 排障

| 现象 | 先查什么 |
| --- | --- |
| `fastboot boot` 报 `FAILED (remote: 'unknown command')` | 手机没进 bootloader，或 USB 驱动；`fastboot devices` 是否为空 |
| `FAILED (remote: 'Boot image does not match device')` 之类 | bootloader 未解锁，或镜像头不是 v3 |
| 按下后立刻黑屏/重启 | 固件卷基址/大小不对（`boot.json` 里的 `fd_base`/`fd_size` 默认应是 `0xA7100000`/`0x300000`）；固件卷超出 ABL 窗口 |
| 停在小米/Android logo | BootShim 没跳过去：看 `boot.json` 的 `shim_bytes`、FD sha256 是否和构建一致 |
| 屏幕有花屏/竖条但没有文字 | 面板是 1080×2400 DSC command mode（`m3_38_0c_0a`），`fuxi.dsc` 的 `PcdFrameBuffer*` 与 ABL 的 scan-out stride 必须一致 |
| UEFI 有输出，没菜单 | `UniXNU.efi`/`UniXNUDxe.efi` 是否进了 FV（`fuxi.fdf`、`Include/DXE.inc`）；GOP 是否发布 |
| 菜单有，没有 xnu 项 | payload 没进 ramdisk 字段，或 FAT 卷标不是 `FUXIESP`，或 `EFI/UniXNU/unixnu.toml` 路径不对 |
| 内核入口后 panic，显示 provider 名 | ADT 缺节点，或该 provider 的驱动没链接；把 panic 文字发出来 |
| 卡在 UniXNU 菜单 | 键位不对：fuxi 的电源/音量键驱动（`FuxiKeyInputDxe`）在这一版里是注释掉的，只能等超时自动启动 |
| 想确认镜像结构 | `python3 uefi/tools/mkbootimg_v3.py info --image boot.img` |

### 取日志

- 屏幕：panic 屏/启动进度是唯一可靠输出。
- GitHub Actions：artifact 里的 `out/logs/*.log` 有每一步的构建日志；构建失败先看这个。
- 如果以后接上 UART（拆机焊点），`serial=3` 已经在内核参数里。

## 6. 回滚

本流程不写分区，所以：

```sh
fastboot reboot       # 或长按电源 10 秒
```

如果你以后手动 `fastboot flash boot` 过，恢复方法是从同版本官方 ROM 包里提取
`boot.img`/`init_boot.img` 再 `fastboot flash` 回去。

## 7. 进阶：userdata（现在不需要）

要真正进 iPadOS 用户态，还需要：

1. 用 IPSW 的 `OS`/`Cryptex1,SystemOS` 磁盘镜像生成 userdata 原始镜像
   （`./unixnu build userdata`，需要 macOS 的 `hdiutil`）；
2. 确认你手机的容量版本对应的 `[userdata]` LBA：

   ```toml
   first_lba = 0x2F7840
   last_lba  = 0x76923FA
   ```

   这是 **512 GB 版本**的取值；256 GB 版本不同，必须用手机的 GPT 重新读
   （`ls -l /dev/block/by-name/userdata` 或 `sgdisk -p`）。
3. `fastboot flash userdata out/fuxi/userdata/userdata-root.img`（**会清空数据**）。

在显示/触控/存储驱动就绪之前，这一步没有意义。

## 8. 安全与合规

- 解锁 BL 会清数据、失去保修、部分金融 App 不可用；
- 不要在有重要数据的机器上做实验；
- 构建产物包含 Apple 恢复镜像里的内核/信任缓存，**不要公开分发**；
- 上游 UniXNU/Mu-Silicium 各自遵循其许可证。