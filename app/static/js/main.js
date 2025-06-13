document.addEventListener('DOMContentLoaded', () => {
    const scrapeForm = document.getElementById('scrape-form');
    const runButton = document.getElementById('run-button');
    const statusArea = document.getElementById('status-area');
    const resultArea = document.getElementById('result-area');
    let eventSource = null;

    scrapeForm.addEventListener('submit', (event) => {
        event.preventDefault();

        // 既存の接続があれば閉じる
        if (eventSource) {
            eventSource.close();
        }

        runButton.disabled = true;
        runButton.textContent = '処理中...';
        statusArea.textContent = 'サーバーに接続しています...';
        resultArea.innerHTML = '';

        const formData = new FormData(scrapeForm);
        const areaId = formData.get('area_id');
        const selectedAreaName = scrapeForm.querySelector('select[name="area_id"] option:checked').textContent;
        
        eventSource = new EventSource(`/scrape?area_id=${areaId}`);

        eventSource.onopen = () => {
            statusArea.textContent = 'サーバーとの接続が確立されました。処理を開始します...';
        };

        eventSource.addEventListener('message', (e) => {
            statusArea.textContent = e.data;
        });

        eventSource.addEventListener('progress', (e) => {
            const progress = JSON.parse(e.data);
            statusArea.textContent = `サロン詳細情報を取得中... (${progress.current}/${progress.total}件)`;
        });

        eventSource.addEventListener('result', (e) => {
            const result = JSON.parse(e.data);
            statusArea.textContent = `「${selectedAreaName}」の処理が完了しました。`;

            const downloadLink = document.createElement('a');
            downloadLink.href = `/download/${result.file_name}`;
            downloadLink.textContent = `${result.file_name} をダウンロード`;
            resultArea.appendChild(downloadLink);

            eventSource.close();
            runButton.disabled = false;
            runButton.textContent = 'スクレイピング実行';
        });

        eventSource.onerror = (e) => {
            let errorMessage = 'サーバーとの接続でエラーが発生しました。';
            try {
                // EventSourceのエラーイベントは詳細を直接渡さないため、
                // 最後のメッセージや一般的なエラーを示す。
                if (e.data) {
                    const errorData = JSON.parse(e.data);
                    errorMessage = errorData.error;
                }
            } catch (parseError) {
                // Do nothing, use the generic error message
            }
            
            statusArea.textContent = 'エラーが発生しました。';
            resultArea.textContent = errorMessage;
            
            eventSource.close();
            runButton.disabled = false;
            runButton.textContent = 'スクレイピング実行';
        };
    });
}); 