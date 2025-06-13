document.addEventListener('DOMContentLoaded', () => {
    const scrapeForm = document.getElementById('scrape-form');
    const runButton = document.getElementById('run-button');

    // Custom select-box logic
    const customSelectContainer = document.getElementById('custom-select-container');
    const searchInput = document.getElementById('area-search-input');
    const selectedAreaIdInput = document.getElementById('selected-area-id');
    const optionsList = document.getElementById('area-options-list');
    const options = optionsList.querySelectorAll('.area-option');
    let selectedOption = null;

    searchInput.addEventListener('focus', () => {
        optionsList.style.display = 'block';
        filterOptions(''); // Show all options on focus
    });

    searchInput.addEventListener('input', () => {
        filterOptions(searchInput.value);
    });

    function filterOptions(searchTerm) {
        const lowerCaseSearchTerm = searchTerm.toLowerCase();
        options.forEach(option => {
            const areaName = option.textContent.toLowerCase();
            const isMatch = areaName.includes(lowerCaseSearchTerm);
            option.classList.toggle('hidden', !isMatch);
        });
    }

    options.forEach(option => {
        option.addEventListener('click', () => {
            selectOption(option);
            optionsList.style.display = 'none';
        });
    });

    function selectOption(option) {
        if (selectedOption) {
            selectedOption.classList.remove('selected');
        }
        selectedOption = option;
        selectedOption.classList.add('selected');
        
        searchInput.value = option.textContent;
        selectedAreaIdInput.value = option.dataset.value;
    }

    document.addEventListener('click', (e) => {
        if (!customSelectContainer.contains(e.target)) {
            optionsList.style.display = 'none';
        }
    });

    const buttonText = runButton.querySelector('.button-text');
    const statusCard = document.getElementById('status-card');
    const statusTitle = document.getElementById('status-title');
    const statusDetails = document.getElementById('status-details');
    const progressBar = document.getElementById('progress-bar');
    const resultCard = document.getElementById('result-card');
    let eventSource = null;

    scrapeForm.addEventListener('submit', (event) => {
        event.preventDefault();

        if (!selectedAreaIdInput.value) {
            // Simple validation: Show an alert or a more elegant message
            alert('エリアを選択してください。');
            return;
        }

        if (eventSource) {
            eventSource.close();
        }

        // UIを処理中状態に設定
        runButton.disabled = true;
        runButton.classList.add('loading');
        statusCard.style.display = 'flex';
        statusCard.style.animation = 'fadeInUp 0.5s ease-out forwards';
        statusTitle.textContent = '接続中...';
        statusDetails.textContent = 'サーバーとの接続を確立しています。';
        progressBar.style.width = '0%';
        resultCard.style.display = 'none';
        resultCard.innerHTML = '';

        const areaId = selectedAreaIdInput.value;
        
        eventSource = new EventSource(`/scrape?area_id=${areaId}`);

        eventSource.onopen = () => {
            statusTitle.textContent = '処理開始';
            statusDetails.textContent = 'サーバーとの接続が確立されました。';
        };

        eventSource.addEventListener('message', (e) => {
            statusTitle.textContent = '情報収集中';
            statusDetails.textContent = e.data;
        });

        eventSource.addEventListener('url_progress', (e) => {
            const progress = JSON.parse(e.data);
            statusTitle.textContent = 'URL収集中';
            statusDetails.textContent = `サロン一覧ページをスキャンしています... (${progress.current}/${progress.total}ページ)`;
            if (progress.total > 0) {
                progressBar.style.width = `${(progress.current / progress.total) * 100}%`;
            }
        });

        eventSource.addEventListener('progress', (e) => {
            const progress = JSON.parse(e.data);
            statusTitle.textContent = '詳細情報取得中';
            statusDetails.textContent = `サロン詳細情報を取得しています... (${progress.current}/${progress.total}件)`;
            if (progress.total > 0) {
                progressBar.style.width = `${(progress.current / progress.total) * 100}%`;
            }
        });

        eventSource.addEventListener('result', (e) => {
            const result = JSON.parse(e.data);
            statusCard.style.display = 'none';
            showResultCard(true, `処理が正常に完了しました。`, `ファイル名: ${result.file_name}`, result.file_name, result.preview_data);
            resetUI();
        });

        eventSource.onerror = (e) => {
            let errorMessage = 'サーバーとの接続で予期せぬエラーが発生しました。';
            // 詳細なエラーメッセージの取得を試みる (ただし通常は限定的)
             try {
                if (e.data) {
                    const errorData = JSON.parse(e.data);
                    if (errorData.error) {
                        errorMessage = errorData.error;
                    }
                }
            } catch (parseError) { /* ignore */ }
            
            statusCard.style.display = 'none';
            showResultCard(false, 'エラーが発生しました', errorMessage, null, null);
            resetUI();
        };
    });

    function showResultCard(isSuccess, title, message, fileName, previewData) {
        let previewHtml = '';
        if (isSuccess && previewData && previewData.length > 0) {
            const headers = Object.keys(previewData[0]);
            const headerHtml = headers.map(header => `<th>${header}</th>`).join('');

            const rowsHtml = previewData.map(row => {
                const cellsHtml = headers.map(header => `<td>${row[header] || ''}</td>`).join('');
                return `<tr>${cellsHtml}</tr>`;
            }).join('');

            previewHtml = `
                <div class="preview-container">
                    <p class="preview-title">データプレビュー (先頭${previewData.length}件)</p>
                    <div class="table-wrapper">
                        <table>
                            <thead>
                                <tr>${headerHtml}</tr>
                            </thead>
                            <tbody>
                                ${rowsHtml}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }

        resultCard.innerHTML = `
            <div class="result-icon ${isSuccess ? 'success' : 'error'}">
                <svg viewBox="0 0 24 24" class="${isSuccess ? 'success-icon' : 'error-icon'}">
                    ${isSuccess 
                        ? '<path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>' 
                        : '<path d="M11 15h2v2h-2zm0-8h2v6h-2zm.99-5C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z"/>'
                    }
                </svg>
            </div>
            <h2 class="result-title">${title}</h2>
            <p class="result-message">${message}</p>
            ${fileName ? `<a href="/download/${fileName}" class="download-link">ファイルをダウンロード</a>` : ''}
            ${previewHtml}
        `;
        resultCard.style.display = 'flex';
        resultCard.style.animation = 'fadeInUp 0.5s ease-out forwards';
    }

    function resetUI() {
        if(eventSource) eventSource.close();
        runButton.disabled = false;
        runButton.classList.remove('loading');
    }
}); 