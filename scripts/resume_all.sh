#!/bin/zsh
# Restart every collector after a reboot. Each is resumable (skips what it already has); any already
# running is left alone. Logs go to logs/<name>.log.
cd "${0:A:h}/.."
run() {  # run <log-name> <collector args...>
  local name=$1; shift
  if pgrep -f "collectors/$*" >/dev/null; then echo "running: $*"; return; fi
  nohup uv run --project . python collectors/"$@" >> "logs/$name.log" 2>&1 &
  echo "started: $*"
}
run by_mfa_en by_mfa.py --lang en
run by_mfa_ru by_mfa.py --lang ru
run by_president_en by_president.py --lang en
run by_president_ru by_president.py --lang ru
run in_mea in_mea.py
run kp_rodong_en kp_rodong_en.py
run pk_mofa pk_mofa.py
run ru_kremlin ru_kremlin.py
run ru_mid ru_mid.py
run ru_mil ru_mil.py
run ru_scrf ru_scrf.py
run ru_ria_ru ru_statemedia.py ria_ru --follow
run ru_rt_com ru_statemedia.py rt_com --follow
run ru_rt_ru ru_statemedia.py rt_ru --follow
run ru_tass_com ru_statemedia.py tass_com --follow
run ru_tass_ru ru_tass_ru.py rss --follow
run ru_telegram ru_telegram.py medvedev_telegram MariaVladimirovnaZakharova
run us_state us_state_briefings.py
run us_whitehouse_biden us_whitehouse.py --site biden
run us_whitehouse us_whitehouse.py --site current
# ISPR sits behind Cloudflare: retry via Wayback every 30 minutes.
pgrep -f "collectors/pk_ispr.py" >/dev/null || { nohup zsh -c 'while true; do uv run --project . python collectors/pk_ispr.py; sleep 1800; done' >> logs/pk_ispr.log 2>&1 & echo "started: pk_ispr loop"; }
# Taiwan (added 2026-10-02 night)
run tw_ey_en tw_ey.py --lang en
run tw_ey_zh tw_ey.py --lang zh
run tw_mofa_en tw_mofa.py --lang en
run tw_mofa_zh tw_mofa.py --lang zh
run tw_focustaiwan tw_media.py focustaiwan --follow
run tw_cna tw_media.py cna --follow
run tw_taipeitimes tw_media.py taipeitimes --follow
# MAC is Wayback-only (live site behind a Cloudflare challenge): retry every 30 minutes.
pgrep -f "collectors/tw_mac.py" >/dev/null || { nohup zsh -c 'while true; do uv run --project . python collectors/tw_mac.py --lang zh; sleep 1800; done' >> logs/tw_mac_zh.log 2>&1 & echo "started: tw_mac_zh loop"; }
# Not enabled (needs Jonathan's OK + a stop-after-failures guard): run tw_president_en tw_president.py --lang en
# Türkiye (added 2026-10-02 night)
run tr_mfa_en tr_mfa.py --lang en
run tr_mfa_tr tr_mfa.py --lang tr
run tr_tccb_en tr_tccb.py --lang en
run tr_tccb_tr tr_tccb.py --lang tr
run tr_aa_en tr_aa.py --lang en recent --follow
run tr_aa_tr tr_aa.py --lang tr recent --follow
run tr_aa_en_backfill tr_aa.py --lang en backfill
run tr_aa_tr_backfill tr_aa.py --lang tr backfill
# Iran expansion (added 2026-10-02 night)
run ir_president ir_president.py --follow
run ir_khamenei_en ir_khamenei.py --lang en
run ir_presstv ir_presstv.py --follow
# Not enabled until its parser is checked on a real page: run ir_khamenei_fa ir_khamenei.py --lang fa
# Syria / Venezuela / Cuba (added 2026-10-02 night)
run sy_sana_new sy_sana.py --part new
run sy_mofa sy_mofa.py
run ve_mppre ve_mppre.py
run cu_granma cu_granma.py
run cu_minrex cu_minrex.py
# archive.sana.sy is slow and often answers 503: re-run every 30 min (retries failed articles first).
pgrep -f "collectors/sy_sana.py --part archive" >/dev/null || { nohup zsh -c 'while true; do uv run --project . python collectors/sy_sana.py --part archive; sleep 1800; done' >> logs/sy_sana_archive.log 2>&1 & echo "started: sy_sana archive loop"; }
# Gap-fill (added 2026-10-03)
run ru_tass_ru_backfill ru_tass_ru.py backfill
run by_belta_en by_belta.py --follow
run cn_mfa_live cn_mfa_live.py --follow
run ru_duma ru_duma.py --follow
