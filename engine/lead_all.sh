#!/usr/bin/env bash
# Second vocal separation pass: UVR-MDX-NET Karaoke 2 on the mix -> lead vocal; keep only an RMS envelope. One song at a time.
P=/workspace/research/dj-auto-cues/proof; R=$P/real; source $P/env.sh
for i in $(seq -w 1 20); do
  [ -f $R/out/lead/$i.npz ] && continue
  while [ $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) -lt 2500 ]; do sleep 15; done
  W=$(mktemp -d); echo "$i $(date +%T)"
  $P/venv-sep/bin/audio-separator $R/wav/$i.wav -m UVR_MDXNET_KARA_2.onnx --model_file_dir $P/cache/uvr --output_dir $W --output_format WAV > $R/logs/lead_$i.log 2>&1
  $P/venv-sep/bin/python - "$W" "$R/out/lead/$i.npz" <<'PY'
import sys,glob,numpy as np,soundfile as sf
w,out=sys.argv[1],sys.argv[2]; f=[x for x in glob.glob(w+"/*.wav") if "(Vocals)" in x][0]
x,sr=sf.read(f,dtype="float32"); x=x.mean(1) if x.ndim>1 else x; hop=int(sr*0.05); n=len(x)//hop
np.savez_compressed(out,hop_s=np.float32(hop/sr),lead=np.sqrt((x[:n*hop].reshape(n,hop)**2).mean(1)).astype(np.float32)); print("ok")
PY
  rm -rf $W
done; echo ALLDONE
