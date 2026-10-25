# 用上游 loader 启动 iOS（fuxi 实测流程，2026-10-25）

结果：**苹果 logo + 进度条跑完**，随后内核 console 显示系统运行日志（launchd 已拉起 xpcproxy、
bluetoothd、fairplayd、triald、role 等守护进程）。显示仍未交接到 SpringBoard。
整条链路已在小米 13（fuxi, SM8550）上验证。

## 组成

| 部分 | 来源 |
| --- | --- |
| loader + 预编译 XNU payload + GPU 固件 | `bangprovn/ios-fuxi`（`shim/fuxi-loader`、`fuxi-loader-build/`） |
| root 镜像（16 GiB HFSX, iOS 16.7.16 iPhone X） | `uefi-fuxi` 的 iosroot 工作流 |
| ADT extent 表 / cmdline | 本仓库 `scripts/patch-upstream-payload.py` |
| boot.img 打包 | 本仓库 `scripts/pack-loader-v3.py` |

## 前置：root 镜像的属主

iosroot 工作流必须用 **`hdiutil attach -owners on`** 挂载 HFS+ 卷，并在复制后
`sudo chown -R 0:0 /Volumes/iOSRoot`。否则镜像内全部是 uid 501，内核拒绝映射
dyld 共享缓存：

```
shared_region: map('dyld_shared_cache_arm64'): owned by uid=501 instead of 0
```

## 步骤

```sh
# 1. 解压 root 并刷进 userdata（会清空 Android 数据）
gunzip root.img.gz
fastboot flash userdata root.img

# 2. 生成带 extent 的 payload（root 在 userdata 第 0 块）
python3 scripts/patch-upstream-payload.py \
  uefi-fuxi/fuxi-loader-build/payload payload.bin \
  --root-bytes 17179869184 \
  --cmdline 'debug=0xc serial=3 cpus=1 keepsyms=1 -noprogress fuxi_ufs_probe=1 unentitled_ios_sim_launch=1 ignition_level=2 wdt_test=no_user_checkin rd=ufsroot0 ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1'

# 3. 编 loader（Linux，clang/lld/llvm-objcopy）
cd ios-fuxi/shim/fuxi-loader
CLANG=clang LLD=ld.lld OBJCOPY=llvm-objcopy READELF=llvm-readelf MKBOOTIMG=/bin/sh \
FUXI_GMU_FIRMWARE=.../gmu_gen70200.bin \
FUXI_SQE_FIRMWARE=.../a740_sqe.fw \
FUXI_GPU_ZAP_MBN=.../a740_zap.mbn \
bash build.sh /path/to/payload.bin

# 4. 打包并启动
python3 scripts/pack-loader-v3.py --image out/fuxi-loader/Image --out fuxi-boot.img
fastboot boot fuxi-boot.img
```

## 关键 boot-arg

| 参数 | 作用 |
| --- | --- |
| `fuxi_ufs_probe=1` | 打开 fuxi UFS 驱动（没有它直接 ENXIO） |
| `rd=ufsroot0` `ufsrw=1` `rootrw=1` | 以 extent root 为可写根 |
| `ignition_level=2` | 允许 graft（dyld 缓存在 cryptex 里时需要） |
| `unentitled_ios_sim_launch=1` | launchd 以无 entitlement 方式启动 |
| `launchd_unsecure_cache=1` | launchd 接受 job cache，不经过 AMFI |
| `cpus=1` / `cpus=6` | 单核（慢，安全）/ 多核 |

## 实测日志（关键行）

```
FUXI UFS: root file in userdata: 1 extents, 4194304 blocks
SWEET UFS: "userdata" on LUN 0: first block 0x2f7840, 16384 MiB, read-write
BSD root: ufsroot0, major 1, minor 0
LibIgnition: ignition sequence complete
dyld: (共享缓存映射成功)
... 苹果 logo + 进度条 ...
```

## 下一步

进度条完成后黑屏，属于**显示/GPU 交付**阶段。相关上游文档：

- `docs-lanes/gpu.md`：`fuxigpu=1 fuxigpuabi=1`、`fuxigpulevel`
- `docs-lanes/touch-display.md`：`sweetfb_hz`、面板交付
- `docs-lanes/sys-port.md`：`fuxiprivs=1`、`fuxisbpath=1`

可以先试：

```
... fuxigpu=1 fuxigpuabi=1 fuxiprivs=1 fuxisbpath=1 ...
```
## 实测：用户态已完整运行（2026-10-25）

```
SWEET MRS: triald ...
exec xpcproxy
exec launchd
exec bluetoothd
exec fairplayd
exec xpcrole
```

- launchd 持续拉起守护进程，系统处于运行状态；
- 屏幕显示的是**内核 console**，不是 SpringBoard 的 UI；
- 下一步是显示/GPU 交付：SpringBoard 需要拿到 framebuffer/GPU 合成通路
  （上游 `docs-lanes/gpu.md`、`touch-display.md`、`sys-port.md`）。

### 可选 boot-arg（尚未验证生效）

```
fuxigpu=1 fuxigpuabi=1 fuxigpulevel=4 fuxiprivs=1 fuxisbpath=1 sweetfb_noconsole=1
```

`sweetfb_noconsole=1` 让内核 console 不再占用 framebuffer，可能是 UI 出现的先决条件。
