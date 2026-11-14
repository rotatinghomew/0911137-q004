import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as directory:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'experiment_family', '--port', str(port), '--state', str(Path(directory) / 'state.sqlite3')], cwd=ROOT)
        base = f'http://127.0.0.1:{port}'
        try:
            for attempt in range(100):
                try:
                    with urlopen(base + '/api/catalog', timeout=1) as response:
                        catalog = json.load(response)
                    break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError('服务启动超时')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 720})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.wait_for_function("document.querySelector('#status').textContent === '分析已更新'")
                assert page.locator('#results tbody tr').count() > 0
                page.locator('#filters select').select_option(catalog['groups'][0])
                page.get_by_role('button', name='更新分析', exact=True).click()
                page.wait_for_function("document.querySelector('#status').textContent === '分析已更新'")
                page.locator('#save input').fill('浏览器业务核对')
                page.get_by_role('button', name='保存当前分析').click()
                page.wait_for_selector('#history button')
                page.reload()
                page.wait_for_selector('#history button')
                page.locator('#history button').click()
                page.wait_for_function("document.querySelector('#status').textContent.includes('浏览器业务核对')")
                assert page.locator('#filters select').input_value() == catalog['groups'][0]
                saved_scope = page.locator('#filters select').input_value()
                page.get_by_role('button', name='查看资料').click()
                page.wait_for_selector('#raw tbody tr')

                # 确认性分析：保存计划 → 预备检查不消耗额度 → 正式查看 → 刷新后仍可追溯。
                page.locator('#design input[name=title]').fill('浏览器确认性核对')
                page.locator('#design input[name=max_looks]').fill('3')
                page.get_by_role('button', name='保存计划').click()
                page.wait_for_selector('#design-detail:not([hidden])')
                assert page.locator('#designs li').count() >= 1
                assert '剩余查看 3/3' in page.locator('#designs li').first.inner_text()
                page.locator('#look-cutoff').fill('40')
                page.get_by_role('button', name='预备检查（不消耗额度）').click()
                page.wait_for_selector('#readiness table tbody tr')
                readiness_rows = page.locator('#readiness tbody tr').count()
                with urlopen(base + '/api/designs') as response:
                    designs = json.load(response)
                assert designs[0]['looks_used'] == 0, designs[0]
                page.once('dialog', lambda dialog: dialog.accept())
                page.get_by_role('button', name='正式打开结果').click()
                page.wait_for_selector('#look-output table tbody tr')
                look_text = page.locator('#look-output').inner_text()
                assert '第 1 次查看' in look_text
                assert '效应范围' in look_text
                assert '资料指纹' in look_text
                assert '不补零' in look_text
                page.reload()
                page.wait_for_selector('#designs button')
                page.locator('#designs button').first.click()
                page.wait_for_selector('#looks button')
                page.locator('#looks button').first.click()
                page.wait_for_selector('#look-output table tbody tr')
                assert '第 1 次查看' in page.locator('#look-output').inner_text()
                with urlopen(base + '/api/designs') as response:
                    designs = json.load(response)
                assert designs[0]['looks_used'] == 1, designs[0]
                assert not errors, errors
                print(json.dumps({'结果': '通过', '页面错误': errors, '保存范围': saved_scope, '计划剩余查看': designs[0]['looks_remaining'], '预备检查行数': readiness_rows}, ensure_ascii=False))
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
