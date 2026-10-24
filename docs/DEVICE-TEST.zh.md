# 上机测试协议与数据采集

这份文档解决两件事：**每次上机怎么测、测完要给我什么**；以及**哪些数据只能从你的手机上取**，
取到之后我可以把 board.toml 里的值改准（尤其是 userdata 的 LBA 和固件卷窗口）。

> 所有测试都只用 `fastboot boot`，不写分区。长按电源 10 秒即可回到 Android。

## 0. 一次测试要回答的问题

| 问题 | 怎么看 |
| --- | --- |
| ABL 接受这个镜像吗？ | `fastboot boot` 的返回：`Booting OKAY` / `FAILED (...)` |
| BootShim 跳过去了吗？ | 屏幕有没有任何变化（花屏也算） |
| UEFI 跑起来了吗？ | 屏幕有没有文字/菜单 |
| UniXNU 菜单出现了吗？ | 有没有 `UNIXNU:` 字样或菜单项 |
| 内核进到了哪一步？ | panic 屏上的文字（拍下来） |

## 1. 测试 A：UEFI-only 镜像

产物：`boot-uefi-only.img`（本地构建）或 workflow `mode=uefi` 的 artifact。

```sh
adb devices
adb reboot bootloader
fastboot devices
fastboot getvar all > getvar.txt 2>&1      # 留档：版本、槽位、解锁状态
fastboot boot boot-uefi-only.img
```

- 开机后 **30 秒内**录像或连拍（屏幕可能一闪而过、也可能黑屏）；
- 卡住/黑屏：长按电源 10 秒；
- 屏幕有任何文字：手机拍照，尽量拍全。

**不要**在这一步用 `fastboot flash`。

## 2. 测试 B：full 镜像（带 XNU）

1. 先在 GitHub Actions 跑 `mode=full`（`chip_id` 用 `openssl rand -hex 8` 生成）；
2. 下载 artifact 里的 `boot.img`；
3. 同上，`fastboot boot boot.img`；
4. 这一版没有显示驱动，能看到的最后画面通常是 **panic 屏**——它是最有价值的输出。

## 3. 只有你的手机能提供的数据

### 3.1 userdata 的 LBA（决定能不能进用户态）

`board.toml` 现在写的是 512 GB 版本的取值。256 GB 版本不一样，必须重读。

需要 root（Magisk）或 TWRP：

```sh
adb shell su -c 'dd if=/dev/block/sda of=/sdcard/gpt.bin bs=4096 count=34'
adb pull /sdcard/gpt.bin
python3 scripts/gpt-userdata.py gpt.bin
```

输出会直接给出可粘贴的 `[userdata]` 块：

```toml
first_lba = 0x2f7840
last_lba  = 0x76923fa
lba_bytes = 4096
# 462.4 GiB, 121220027 sectors
```

> 没有 root 也能看容量：`adb shell 'cat /proc/partitions'` 里 `sda` 的总大小能区分
> 256 GB / 512 GB，但拿不到偏移。

### 3.2 固件卷窗口（决定固件能不能启动）

`fuxi.toml` 的注释和实际值不一致（见 README）。用你自己机器的 `xbl_config_a` 来定：

```sh
adb shell su -c 'dd if=/dev/block/by-name/xbl_config_a of=/sdcard/xblcfg.bin'
adb pull /sdcard/xblcfg.bin
python3 scripts/blob-scan.py xblcfg.bin --find "UEFI FD"
```

看输出里的 `candidate: base ... size ...`：

- `base 0xa7000000 size 0x0400000` → 说明注释是对的，`[uefi_fd]` 应该改成 4 MiB；
- `base 0xa7100000 size 0x0300000` → 当前值正确（上游 `ad576f83` 的选择）；
- 两个都出现 → 以设备配置（二进制布局）那条为准。

要试另一种布局，在 workflow 里填 `fd_base`/`fd_size` 即可，不用改代码。

### 3.3 其他顺手能给的

```sh
fastboot getvar current-slot
fastboot getvar version-baseband
adb shell getprop ro.build.fingerprint      # 确认是 fuxi / HyperOS 版本
```

## 4. 反馈模板

把下面这段填好发回来，我就能直接改配置或判断卡在哪：

```
机型/容量：fuxi 12+256 / 12+512 / 其他
系统：HyperOS x.x.x.x（ro.build.fingerprint）
是否解锁：是/否
镜像：uefi-only / full（附 boot.json 里的 image_sha256）
fastboot boot 返回：Booting OKAY / FAILED（原文）
屏幕现象：（黑屏 / 花屏 / 停在 logo / 有文字 / 有菜单 / panic）
照片或录像：有/无
getvar.txt：有/无
```

## 5. 恢复

- `fastboot reboot`，或长按电源 10 秒；
- 本流程不写分区，不需要救砖；
- 如果以后 `fastboot flash` 过，用同版本官方 ROM 的 `boot.img` 刷回。

## 6. 别做的事

- 不要在没备份的情况下 `fastboot flash userdata`（会清空全部数据）；
- 不要把 `full` 的 artifact 传到公开仓库（里面有 Apple 的内核/信任缓存）；
- 不要在电量低于 30% 时刷写。
