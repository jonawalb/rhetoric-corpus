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
run ru_kremlin ru_kremlin.py --start earliest --events
run ru_kremlin_en ru_kremlin.py --lang en --start earliest --events
run ru_mid ru_mid.py --start earliest
run ru_mil ru_mil.py --start earliest
run ru_scrf ru_scrf.py --start earliest
run ru_ria_ru ru_statemedia.py ria_ru --follow --full --start earliest
run ru_rt_com ru_statemedia.py rt_com --follow --full --start earliest
run ru_rt_ru ru_statemedia.py rt_ru --follow --full --start earliest
run ru_tass_com ru_statemedia.py tass_com --follow --full --start earliest
run ru_sputnik_en ru_statemedia.py sputnik_en --follow --full --start earliest
run ru_tass_ru ru_tass_ru.py rss --follow
run ru_telegram_live ru_telegram.py --follow
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
# FA parser checked on real Wayback captures 2026-10-03 (div.Content layout); enabled.
run ir_khamenei_fa ir_khamenei.py --lang fa
# Iran deep expansion (added 2026-10-03): Leader's office + state/IRGC-affiliated media, full archives, one process per host.
run ir_leader ir_leader.py --follow
run ir_media_iribnews ir_media.py iribnews --follow
run ir_media_yjc ir_media.py yjc --follow
run ir_media_mizan ir_media.py mizan --follow
run ir_media_kayhan ir_media.py kayhan --follow
run ir_media_javan ir_media.py javan --follow
run ir_media_defapress ir_media.py defapress --follow
run ir_media_sobhesadegh ir_media.py sobhesadegh --follow
run ir_media_snn ir_media.py snn --follow
run ir_media_basijnews ir_media.py basijnews --follow
run ir_media_mashregh ir_media.py mashregh --follow
run ir_media_icana ir_media.py icana --follow
run ir_media_tasnim ir_media.py tasnim --follow
run ir_media_nournews ir_media.py nournews --follow
run ir_media_jamejam ir_media.py jamejam --follow
run ir_media_iqna ir_media.py iqna --follow
run ir_media_shana ir_media.py shana --follow
# MFA English via Wayback only (live site behind an ArvanCloud cookie gate): retry every 30 minutes.
pgrep -f "collectors/ir_mfa.py" >/dev/null || { nohup zsh -c 'while true; do uv run --project . python collectors/ir_mfa.py; sleep 1800; done' >> logs/ir_mfa_en.log 2>&1 & echo "started: ir_mfa loop"; }
# Syria / Venezuela / Cuba (added 2026-10-02 night)
run sy_sana_new sy_sana.py --part new
run sy_mofa sy_mofa.py
run ve_mppre ve_mppre.py
run cu_granma cu_granma.py
run cu_minrex cu_minrex.py
# archive.sana.sy is slow and often answers 503: re-run every 30 min (retries failed articles first).
pgrep -f "collectors/sy_sana.py --part archive" >/dev/null || { nohup zsh -c 'while true; do uv run --project . python collectors/sy_sana.py --part archive; sleep 1800; done' >> logs/sy_sana_archive.log 2>&1 & echo "started: sy_sana archive loop"; }
# Gap-fill (added 2026-10-03)
run ru_tass_ru_backfill ru_tass_ru.py backfill --start earliest
run by_belta_en by_belta.py --follow
run cn_mfa_live cn_mfa_live.py --follow
run ru_duma ru_duma.py --follow --start earliest
# Russia deep expansion (added 2026-10-03): official gazette, VGTRK, Government, Federation Council
run ru_rg_ru ru_sitemap_media.py rg_ru --follow
run ru_vesti_ru ru_sitemap_media.py vesti_ru --follow
run ru_government_ru ru_gov_sites.py government_ru --follow
run ru_council_ru ru_gov_sites.py council_ru --follow
run ru_1tv_ru ru_sitemap_media.py 1tv_ru --follow
run ru_premier_archive_ru ru_gov_sites.py premier_archive_ru
# iz.ru not run: robots.txt answers HTTP 403 to our UA (2026-10-03) -> lib disallows the site
# China expansion (added 2026-10-03): MFA/MND/TAO deep archives + official outlets + state media (all --follow)
run cn_mfa_listings cn_mfa.py --part listings --follow
run cn_mfa_archive cn_mfa.py --part archive --follow
run cn_mnd cn_mnd.py --follow --wayback
run cn_tao cn_tao.py --follow
run cn_xinhua_en cn_xinhua.py --lang en --follow
run cn_xinhua_zh cn_xinhua.py --lang zh --follow
run cn_peoples_daily_epaper cn_peoples_daily.py epaper --follow
run cn_peoples_daily_en cn_peoples_daily.py en --follow
run cn_globaltimes cn_globaltimes.py --follow
run cn_cgtn cn_cgtn.py --follow
run cn_chinadaily_en cn_chinadaily.py --lang en --follow
run cn_chinadaily_zh cn_chinadaily.py --lang zh --follow
run cn_chinamil cn_chinamil.py --follow
run cn_chinamil_wayback cn_chinamil.py --wayback
run cn_govcn_zh cn_govcn.py --parts zhfeeds,gazette --follow
run cn_govcn_en cn_govcn.py --parts en --follow
run cn_govcn_wayback cn_govcn.py --wayback
run cn_qiushi cn_qiushi.py --follow
run cn_embassy cn_embassy.py --follow
# China Coast Guard: live site behind CloudWAF (HTTP 418), Wayback copies only
run cn_ccg cn_ccg.py --follow
