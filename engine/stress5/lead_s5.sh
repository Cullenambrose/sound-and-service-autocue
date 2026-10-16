#!/usr/bin/env bash
# v4 lead-vocal pass (UVR Karaoke 2) for tracks whose allin1 is done; one at a time, RAM-gated
P=/workspace/research/dj-auto-cues/proof; S=$P/stress5; source $P/env.sh; mkdir -p $S/out/lead
for j in $(ls $S/out/allin1/*.json 2>/dev/null | xargs -n1 basename | sed 's/.json//' | sort); do
  [ -f $S/out/lead/$j.npz ] && continue
  while [ $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) -lt 3000 ]; do sleep 15; done
  W=$(mktemp -d)
  nice -n 10 $P/venv-sep/bin/audio-separator $S/wav/$j.wav -m UVR_MDXNET_KARA_2.onnx --model_file_dir $P/cache/uvr --output_dir $W --output_format WAV > $S/logs/lead_$j.log 2>&1
  $P/venv-sep/bin/python - "$W" "$S/out/lead/$j.npz" <<'PY'
import sys,glob,numpy as np,soundfile as sf
w,out=sys.argv[1],sys.argv[2]; f=[x for x in glob.glob(w+"/*.wav") if "(Vocals)" in x][0]
x,sr=sf.read(f,dtype="float32"); x=x.mean(1) if x.ndim>1 else x; hop=int(sr*0.05); n=len(x)//hop
np.savez_compressed(out,hop_s=np.float32(hop/sr),lead=np.sqrt((x[:n*hop].reshape(n,hop)**2).mean(1)).astype(np.float32)); print("ok")
PY
  rm -rf $W; echo "$j lead $(date +%T)"
done
