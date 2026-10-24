# 上机测试协议与数据采集

## 0. 先看这里：现在该试哪张镜像

| 目的 | 镜像 | 预期 |
| --- | --- | --- |
| **payload 发现（当前最需要）** | `boot-uefi-hello-dt.img` | 菜单出现 **fuxi: UEFI hello (no XNU)** |
| 如果上一步黑屏/停住 | `boot-uefi-diag.img` | `FUXIDT:` / `FUXIMAP:` / `FUXIRAMDISK:` 三组日志 |
| 只想确认固件 + 界面 | `boot-uefi-diskdbg.img` | 菜单里 Restart / Power off |
| 界面也没有、要查 GOP | `boot-uefi-probe.img` | `FUXIPROBE: GOP Success/Not Found` 后停机 |

其余镜像（`boot-uefi-only`、`hello-up/down/new/full/latest`、`diskup`、`debugcon`）是
排查过程中的中间产物，保留但已被上表取代。



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

## 1b. 测试 A2：UEFI + hello payload（不需要内核）

本地产物：`out/boot-uefi-hello.img`（203 MB，ramdisk 192 MiB —— 和真实 XNU payload 同尺寸，
所以 fastboot/ABL 的尺寸行为也一并验证了）。
里面是一个 FAT 卷：

```
EFI/UniXNU/unixnu.toml         菜单配置（hello / Restart / Power off）
EFI/UniXNU/Payloads/HelloPayload.efi
```

它比 A 多验证四件事：ramdisk 被 `FuxiRamDiskDxe` 发布成 BlockIo → FAT 驱动挂载 →
UniXNU 读到 `unixnu.toml` → 真的启动一个 EFI 应用。

预期：菜单出现 **fuxi: UEFI hello (no XNU)**，选中后 `HelloPayload` 打印信息或返回菜单。
如果 A 有菜单而 A2 没有 hello 项，问题就在 ramdisk/FAT/config，而不在内核——
这个区分能省掉大量猜测。

```sh
fastboot boot boot-uefi-hello.img
```

## 1c. 诊断镜像：带 FUXIPROBE（默认镜像黑屏时用）

本地产物：`out/boot-uefi-probe.img`（1.85 MB，不含 payload）。

它在固件卷里多编进了一个上游自带、但默认没启用的驱动 `FuxiProbeDxe`。开机后它清屏并打印：

```
FUXIPROBE: walking the memory map for FUXIESP
FUXIPROBE: hit at 0x... label=FUXIESP type=...
```

它只扫描 UEFI 内存映射里属于普通 RAM 的区间（不去碰未映射的 MMIO），命中的地址就是 payload
FAT 卷在内存里的位置；最后故意 `CpuDeadLoop()` 停住。

| 屏幕现象 | 含义 |
| --- | --- |
| 有 `FUXIPROBE:` 文字 | 固件、GOP、ConOut 至少到了能打印的阶段 |
| 有文字、`no FUXIESP volume ...` | 控制台正常，但 ramdisk 没进内存（BootShim/SEC/ramdisk 定位） |
| 有文字、有 `hit` | payload 卷在内存里，问题在 FAT/UniXNU 的发现逻辑 |
| 仍然黑屏并卡住 | 控制台没起来或更早就挂了；长按电源 |

它**不会继续启动**，看到结果后长按电源即可。复现方法见
[BUILD-LOCAL.zh.md](BUILD-LOCAL.zh.md) 的“诊断固件”一节（`apply-fixes.py --probe`）。

## 1d. 屏幕日志镜像（DEBUG 改走 ConOut）

本地产物：`out/boot-uefi-debugcon.img`（1.84 MB）。

先说清楚这台机器上日志是怎么出来的：平台的 `SerialPortLib` 不是真串口，而是
`SiliciumPkg/Library/FrameBufferSerialPortLib` —— 它按内存映射里的 **"Display Reserved"**
（`0xB8000000`，43 MiB）直接把 `DEBUG(...)` 的字符写进帧缓冲。
所以 **DEBUG 日志本来就在屏幕上**，不需要 UART。

这也解释了一个关键区别：

| 输出方式 | 走哪条路 | 屏幕可见？ |
| --- | --- | --- |
| `DEBUG (...)` | FrameBufferSerialPortLib → 帧缓冲 | ✅ 只要该区域可写 |
| `Print (...)` / UniXNU 菜单 | ConOut → SimpleFbDxe 的 GOP → GraphicsConsole | ❌ 如果 GOP 没起来就看不到 |

`out/boot-uefi-debugcon.img` 把 DXE 的 DebugLib 换成 ConOut 版本，是备用手段（一般不需要）。

注意：

- SEC 仍然用串口库（`UefiDebugLibConOut` 不能在 SEC 里用），所以最早的 SEC 日志还是看不到；
- `PrepareRfsProtection` 那条本身**无害**：它在 `Silicon/Qualcomm/QcomPkg/Library/RfsProtectionLib/RfsProtection.c`
  里 locate `gQcomScmProtocolGuid`，因为 `FUXI_VENDOR_HW=0` 没装 vendor 栈（SCM）而打印，然后
  **返回 EFI_SUCCESS**，BdsDxe 继续。
- 如果屏幕上一行日志都没有，说明 ConOut/GOP 还没起来，问题在显示路径而不是后面的模块。

复现：`apply-fixes.py --debug-conout` 后重新编固件，见 [BUILD-LOCAL.zh.md](BUILD-LOCAL.zh.md)。

## 1e. 卡在 `FuxiRamDiskDxe.efi` 时用这个镜像

现象（实测反馈）：屏幕最后一行是

```
Loading driver at 0x00006680000 EntryPoint=0x000C6681050 FuxiRamDiskDxe.efi
```

原因有三层：

1. 这行是 DXE dispatcher 的 `DEBUG`，所以能看见；
2. `FuxiRamDiskDxe` 自己的报告用 `Print()`，要 ConOut；GOP 没起来时全部不可见；
3. 它找不到 FAT 卷（标号 `FUXIESP`）时会执行 `CpuDeadLoop()`——看起来就是“卡死”，
   实际是上游故意停在这里。

本地产物：`out/boot-uefi-diskdbg.img`（1.84 MB）。它把这个驱动改成：

- 关键点用 `DEBUG` 报告（屏幕直接可见）：entry、HOB 提示地址、搜索结果、是否返回；
- 找不到卷时 **不再死循环**，返回错误让 BdsDxe / UniXNU 继续跑（UniXNU 的日志也是 DEBUG）。

预期看到（顺序）：

```
FUXIRAMDISK: entry (debug path)
FUXIRAMDISK: no volume; hob=1 base=0x...      ← 或者 FUXIRAMDISK: payload at 0x...
FUXIRAMDISK: returning NOT_FOUND instead of halting
PrepareRfsProtection: ...
UNIXNU: ...
```

`payload at 0x...` 表示卷找到了；`no volume` 表示 SEC 的 HOB 提示没有命中，
接下来就要看 `base=` 的值和 BootShim 传参。

### 为什么 hint 可能指错地方

固件里 SEC 写死了提示地址（`Silicon/Silicium/SiliciumPkg/Sec/Sec.c`）：

```c
PayloadBase = gSecEntryArg1 + 0x300000;   // the payload sits just below the device tree
```

`gSecEntryArg1` 是 BootShim 传进来的 x1，也就是 `_Payload`（固件卷拷贝源）的地址；
`+0x300000` 正好是 3 MiB 固件卷的末尾。上游的查找逻辑从这点**向下**扫 256 MiB。

但 boot 镜像的 kernel 字段是 `gzip(shim+FD) + DTB`，ramdisk 是独立字段。如果 ABL 把 ramdisk
放在 DTB 之后（更高地址），向下扫永远扫不到——这正好能解释“no volume”。

`out/boot-uefi-diskup.img` 把扫描方向改成**向上 16 MiB**，并用 DEBUG 打印起点：

```
FUXIRAMDISK: scanning up from 0x...
FUXIRAMDISK: payload at 0x... (找到时)
```

两个镜像的用途：

| 镜像 | 扫描方向 | 用途 |
| --- | --- | --- |
| `boot-uefi-diskdbg.img` | 上游（向下 256 MiB） | 先看 `hob=`/`base=`，确认提示值 |
| `boot-uefi-diskup.img` | 向上 16 MiB | 提示值偏低时用 |

如果 `hob=0`，说明 SEC 根本没发布提示（x0 指向的 FDT 没通过 magic/大小检查），
那就不是扫描方向的问题，要先修 SEC 的取值。

## 1f. 已经能进 UniXNU 界面之后

实测（2026-10-24）：用**不 halt 的 ramdisk 驱动**镜像，手机已经能进 UniXNU 界面。这说明：

- BootShim → UEFI → DXE → BdsDxe → UniXNU 这条链是通的；
- UniXNU 的 UI 能画出来（GOP/ConOut 工作）；
- 之前的"卡死"确实就是 `FuxiRamDiskDxe` 找不到卷时的 `CpuDeadLoop()`。

下一步是让 UniXNU 找到 payload 卷并列出条目。以下两个镜像都带 **192 MiB 的 hello payload**：

| 镜像 | 固件里的扫描方向 |
| --- | --- |
| `boot-uefi-hello-latest.img` | 上游 a7f42bfc（提示下方 128 MiB）+ 我们的 /chosen/Kernel 窗口兜底 |
| `boot-uefi-hello-down.img` | 上游的向下 256 MiB |
| `boot-uefi-hello-up.img` | 向上 16 MiB（DT/ramdisk 在 kernel 字段之后） |

预期：

- 菜单里出现 **fuxi: UEFI hello (no XNU)** → 卷找到了，FAT/config/加载整条链在真机上通了；
- 菜单里只有内置项（没有 hello）→ 卷没找到，把屏幕上的 `FUXIRAMDISK:` 行发回来。

## 1g. 一张图看全：`boot-uefi-diag.img`

带 hello payload，同时打开三组诊断（都用 `DEBUG`，屏幕可见）：

| 前缀 | 内容 |
| --- | --- |
| `FUXIRAMDISK:` | 驱动入口、HOB 提示、扫描结果、是否返回 |
| `FUXIDT:` | x0 的值、是否是合法 FDT、`/chosen` 的 `linux,initrd-start/end`、bootargs |
| `FUXIMAP:` | 内存描述符（≥16 MiB，最多 12 条）：基址、大小、类型 |

这三个组合起来能回答：
- ABL 有没有把 ramdisk 地址写进 `/chosen`（有的话直接用，不用扫）；
- 大块 RAM 都在哪（ramdisk 只可能落在这些区域）；
- 当前扫描窗口为什么没命中。

如果 `initrd-start/end` 指向一段 192 MiB 左右的范围，那就是我们的 payload，
下一版固件直接用它，不再扫描。

## 1h. 跟随上游最新：`boot-uefi-hello-new.img`

上游在 2026-10-24 又推了几个提交（`5f5727c1`…`c15fe352`）：

- `6dc976ee` 让 `FuxiRamDiskDxe` **只报告、不清屏、不死循环**；
- `628e5aa3` / `c15fe352` 把它重新编进固件卷。

也就是说我们之前手动打的"不 halt"补丁，上游已经合入。`out/boot-uefi-hello-new.img` 就是基于这个新基线
（外加我们的 DEBUG 输出和向上扫描）编译的，带 192 MiB hello payload。

如果它和 `boot-uefi-hello-up.img` 表现一致，可以确认不是基线差异；如果只有它能进 hello 项，
说明上游顺手修了别的东西。

## 1i. 三段式搜索：`boot-uefi-hello-full.img`

如果 hello 还是找不到，这张镜像会把所有**安全可达**的位置都试一遍（仍然只报告、不停机）：

1. **`/chosen` `linux,initrd-start`**：ABL 如果按 Linux 惯例把 ramdisk 写进设备树，这里就是准确地址；
2. **Kernel 窗口 `0xA8000000..0xB8000000`**：平台内存表里这块是 `SYS_MEM/Reserv`，已映射，读它不会 fault；
3. **所有 Conventional/Loader/BootServices 描述符**（`LocatePayload()`）。

屏幕日志：

```
FUXIRAMDISK: hint miss, trying /chosen and full scans
FUXIRAMDISK: chosen initrd-start=0x...
FUXIRAMDISK: scanning kernel window 0xa8000000..0xb8000000     ← 可能几秒
FUXIRAMDISK: payload at 0x... in the kernel window             ← 命中时
FUXIRAMDISK: conventional memory scan
FUXIRAMDISK: payload at 0x... from the memory map
```

菜单里出现 **fuxi: UEFI hello (no XNU)** 就是找到了。哪怕没找到，`initrd-start` 的值也直接告诉我们 ABL 的想法。

## 1j. 真机日志读出来的内存布局（2026-10-24）

用 `boot-uefi-diag.img` 抓到（屏幕 OCR，数值以设备为准）：

```
FUXIDT: entry x0=0x…7A085000
FUXIDT: fdt ok, chosen=…
FUXIDT: initrd-start=0x…, initrd-end=0xB7FFE…, len=8
FUXIMAP: 55 descriptors
FUXIMAP: A8000000 256 MiB type 0   ← 平台的 "Kernel" 窗口
FUXIMAP: BAB00000 186 MiB type 7   ← DXE heap（Conventional）
FUXIMAP: 89800000 402 MiB type 0
FUXIMAP: A2AB0000  54 MiB type 0
FUXIMAP: D8800000 138 MiB type 0
FUXIRAMDISK: no volume; hob=1 base=0x…C4C2914
FUXIRAMDISK: returning NOT_FOUND instead of halting
```

两条结论：

1. **SEC 的提示地址在低内存**（`base`≈0x0C4C2914，对应 x1+3 MiB），而 192 MiB 的 payload
   只可能落在 `0xA8000000` 那个 256 MiB 的 Kernel 窗口里——所以向下/向上 16 MiB 都扫不到；
2. 设备树里 **`/chosen` 有 `linux,initrd-start/end`**（len=8，64 位），ABL 按 Linux 惯例
   把 ramdisk 地址写进去了——这是最可靠的线索。

因此 `boot-uefi-hello-full.img` 的策略是：

- 先认 `/chosen` `initrd-start`，并且在它 **±4 MiB** 范围内按 512 B 步长找 FAT BPB；
- 第二个候选是 Kernel 窗口 `0xA8000000..0xB8000000` 整段扫描；
- 最后才是常规内存描述符扫描。

预期日志：

```
FUXIRAMDISK: chosen initrd-start=0x...
FUXIRAMDISK: chosen window scan +-4 MiB
FUXIRAMDISK: payload at 0x... near chosen initrd     ← 命中
```

### 再补一层：以设备树为锚点向下扫（`boot-uefi-hello-dt.img`）

上游注释写的是“payload 就在设备树正下方”。如果 ABL 没有改 `/chosen`（还是原厂的
`initrd-start`），那 `/chosen` 就指不到我们的卷，这时唯一可靠的锚点是 **x0 指向的设备树**。

`boot-uefi-hello-dt.img` 在其它尝试之前先做：

```
FUXIRAMDISK: scanning below the device tree 0x...
FUXIRAMDISK: payload at 0x... below the device tree
```

扫描窗口是设备树下方 256 MiB（按 512 B 步长），覆盖“在同一块 bootloader 缓冲里、紧挨着 DT”的情况。

关于 OCR 出来的数值：

- `base=…C4C2914`：`%lx` 不带前导零，所以它其实很可能是 **`0x0C4C2914`**（≈206 MB），
  低于上游新扫描的门槛 `0x40000000`，所以上游那个 128 MiB 扫描在 fuxi 上**不会执行**；
- `x0=…7A085000`：很可能丢了最高位，真实值是 **`0xBA085000`** 一类的高地址（正好在
  Kernel 窗口尾部/紧邻），和“payload 在 DT 下方”的说法一致。

## 1k. 第二次真机日志（2026-10-24）

```
FUXIRAMDISK: entry (debug path)
FUXIRAMDISK: scanning below the device tree A00B5000
FUXIRAMDISK: chosen initrd-start=A02B5000
FUXIRAMDISK: chosen window scan +-4 MiB
FUXIRAMDISK: scanning kernel window 0xa8000000..0xb8000000
FUXIRAMDISK: conventional memory scan
FUXIRAMDISK: no volume; hob=1 base=0xC4C29B14
```

解读：

- DT 在 `0xA00B5000`，`/chosen` 的 `initrd-start = 0xA02B5000`（正好 DT + 2 MiB）；
- 我们传进去的 `fuxi.dtb` 里 `initrd-start` 原本是 `0xB6017000`，所以 **ABL 确实改写了 /chosen**；
- 但 `0xA02B5000` 附近没有 FAT BPB——192 MiB 的 payload 没落在那里，或者根本没被加载。

因此新增两张镜像：

| 镜像 | 作用 |
| --- | --- |
| `boot-uefi-hello-dump.img` | 打印 `initrd` 的 `start..end`、头 32 字节 hexdump，并扫描 initrd 所在的内存描述符 |
| `boot-uefi-hello-64.img` | 同样固件，但 payload 只有 **64 MiB**，用来验证 ABL 是否对 ramdisk 大小有限制 |
| `boot-uefi-hello-64gz.img` | ramdisk 字段是 **gzip(64 MiB FAT)**（压缩后 77 KB）：有些 ABL 期待压缩 ramdisk 并解压到 initrd-start。打包器现支持 `--gzip-ramdisk` |

真实 payload 也可以一键缩小：`apply-fixes.py --payload-size 64` 会把 `[payload] size_mib` 改成 64
（内核约 46 MB，64 MiB 卷放得下）。

预期日志：

```
FUXIRAMDISK: chosen initrd A02B5000..A??????? size=...
FUXIRAMDISK: [A02B5000] eb3c90... / 00000000 ...
FUXIRAMDISK: initrd region 89800000..a2a00000 type=0
FUXIRAMDISK: hint region 80000000..c0000000 type=...     ← FD 缓冲所在描述符
FUXIRAMDISK: payload at ... in the initrd region      ← 命中
FUXIRAMDISK: payload at ... in the hint region        ← 命中
```

三张镜像按顺序试：

1. `hello-dump`（192 MiB）：看 `initrd` 处到底有没有数据；
2. `hello-64`（64 MiB）：全 0 的话，验证是不是大小限制；
3. `hello-64gz`（1.9 MB）：验证 ABL 是不是期待压缩 ramdisk。

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
## 1l. 如果 ABL 忽略 ramdisk 字段：payload 走 kernel 字段

两次真机日志都显示 `/chosen` 的 initrd 是 LZ4 压缩的 vendor ramdisk，我们的 FAT 始终没出现
（192 MiB 和 64 MiB 都一样）。如果 `boot-uefi-hello-any64.img` 也扫不到，基本可以确认
**fuxi 的 ABL 在 `fastboot boot` 时不加载 boot image 的 ramdisk**。

这其实就是**上游设计的本意**。`Sec.c` 里写着：

```c
// The boot image carries the UniXNU payload right behind the device tree.
// Publish its address so the payload block driver does not have to guess.
PayloadBase = gSecEntryArg1 + 0x300000;  // the payload sits just below the device tree
```

而 `BootShim.S` 把 `x1 = _Payload`（解压后 FD 的起点）传给 SEC，所以 `x1 + 3 MiB` 只是
"FD 末尾"的近似值——真正可靠的位置是 **DTB 之后**。

备选传输：把 payload 追加在 **kernel 字段里的设备树之后**。

```
kernel field = gzip(BootShim + FD) + fuxi.dtb + payload
ramdisk field = 空
```

- BootShim 解压 gzip 后仍然在流末尾找到 DTB，把它传给 x0；
- 驱动（`hello-any*` 固件）会扫描 kernel 字段所在的 DRAM 描述符，从而找到 payload；
- 打包命令：

```sh
pack-bootimg-v3.py ... --ramdisk payload-hello-64.img --payload-after-dtb --out boot.img
```

镜像：`out/boot-uefi-hello-kdtb.img`（69 MB，kernel 字段 68.9 MB，ramdisk 为空）。
驱动会先打印 `device tree <start>..<end>`，然后检查 DTB 末尾是否紧跟 FAT 卷
（`payload at ... after the device tree`）。