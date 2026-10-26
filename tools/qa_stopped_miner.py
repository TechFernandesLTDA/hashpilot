"""Valida UI parada com renda preservada, em fixtures sem mineração/hardware."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import monitor
from test_responsive_ui import build_harness,inspect_widgets,descendants
from test_rig_ui import fixture

reports=[]
monitor.enable_native_dpi_awareness()
with tempfile.TemporaryDirectory() as temporary:
    folder=Path(temporary)
    with patch.object(monitor,'STATE_DIR',folder):
        root,_=build_harness(monitor,folder);root.maxsize(9000,9000)
        root.finance_state={}
        data=fixture(1);device=data['rig']['devices'][0]
        device.update(miner_running=False,state='error',process_error='fixture: file unavailable')
        data['miner_executable_available']=False
        finance={'price_brl':10,'price_usd':2,'unpaid_prl':1,'accumulated_prl':1,'accumulated_brl':10,
            'periods':{'day':{'prl':1,'brl':10}},'currencies':{'day':{'prl':1,'brl':10,'usd':2,'btc':.00002}},'forecast':None,
            'payout':{'eta_label':'3 h','remaining_prl':.01}}
        root.apply(data)
        with patch.object(monitor,'financial_model',return_value=finance):root.refresh_finance()
        assert root.status.cget('text').endswith('Minerador indisponível')
        assert root.card_values['hash'].cget('text')=='—'
        assert root.overview_values['today'].cget('text')=='R$ 10,00'
        assert 'rig local parado' in root.finance_explanation.cget('text')
        assert 'Sem previsão local' in root.payout_eta.cget('text')
        assert 'Segurança do minerador' in root.footer.cget('text')
        assert any(getattr(w,'cget',lambda k:None)('text')=='HashPilot '+monitor.APP_BUILD['version'] for w in descendants(root) if w.winfo_class()=='Label')
        snapshot=json.loads((folder/'latest.json').read_text(encoding='utf-8'))
        assert snapshot['app']['version']==monitor.APP_BUILD['version']
        for width,height in [(1264,628),(1920,1080),(3840,2160)]:
            root.geometry(f'{width}x{height}+0+0');root.update();root.adapt_layout();root.update()
            for tab in range(3):
                root.tabs.select(tab);root.update()
                reports.append({'size':[width,height],'tab':tab,'errors':inspect_widgets(root)})
        root.destroy()
print(json.dumps({'cases':len(reports),'errors':[r for r in reports if r['errors']],'assertions':'stopped status, unavailable executable, preserved earnings, visible executing version'},ensure_ascii=True))
raise SystemExit(any(r['errors'] for r in reports))
