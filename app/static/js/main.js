document.addEventListener('DOMContentLoaded', () => {
    const scrapeForm = document.getElementById('scrape-form');
    const runButton = document.getElementById('run-button');

    // Custom select-box logic
    const customSelectContainer = document.getElementById('custom-select-container');
    const formCard = customSelectContainer.closest('.card'); // Get the parent card for z-index control
    const searchInput = document.getElementById('area-search-input');
    const selectedAreaIdInput = document.getElementById('selected-area-id');
    const optionsList = document.getElementById('area-options-list');
    const options = optionsList.querySelectorAll('.area-option');
    let selectedOption = null;
    let activeOptionIndex = -1;
    let visibleOptions = [];

    function updateVisibleOptions() {
        visibleOptions = Array.from(optionsList.querySelectorAll('.area-option:not(.hidden)'));
    }

    function openOptionsList() {
        optionsList.style.display = 'block';
        customSelectContainer.setAttribute('aria-expanded', 'true');
        formCard.style.zIndex = 10;
        updateVisibleOptions();
    }

    function closeOptionsList() {
        optionsList.style.display = 'none';
        customSelectContainer.setAttribute('aria-expanded', 'false');
        formCard.style.zIndex = 'auto';
        activeOptionIndex = -1;
        removeActiveDescendant();
    }

    searchInput.addEventListener('focus', () => {
        openOptionsList();
        filterOptions(''); // Show all options on focus
    });

    searchInput.addEventListener('input', () => {
        filterOptions(searchInput.value);
        openOptionsList();
        activeOptionIndex = -1; // Reset active option on new input
        removeActiveDescendant();
    });

    function filterOptions(searchTerm) {
        const lowerCaseSearchTerm = searchTerm.toLowerCase();
        const groups = optionsList.querySelectorAll('.area-group');
        
        groups.forEach(group => {
            let groupHasVisibleOptions = false;
            // A more robust implementation might use data attributes to link groups and options.
            // For now, let's find the options associated with this group.
            const optionsUnderGroup = [];
            let nextElement = group.nextElementSibling;
            while(nextElement && nextElement.classList.contains('area-option')) {
                optionsUnderGroup.push(nextElement);
                nextElement = nextElement.nextElementSibling;
            }

            optionsUnderGroup.forEach(option => {
                const areaName = option.textContent.toLowerCase();
                const isMatch = areaName.includes(lowerCaseSearchTerm);
                option.classList.toggle('hidden', !isMatch);
                if (isMatch) {
                    groupHasVisibleOptions = true;
                }
            });
            
            // A group is visible if its name matches or it has visible options
            const prefectureName = group.textContent.toLowerCase();
            const isGroupMatch = prefectureName.includes(lowerCaseSearchTerm);
            
            group.classList.toggle('hidden', !isGroupMatch && !groupHasVisibleOptions);
        });
        updateVisibleOptions();
    }

    options.forEach(option => {
        option.addEventListener('click', () => {
            selectOption(option);
            closeOptionsList();
        });
    });

    function selectOption(option) {
        if (selectedOption) {
            selectedOption.classList.remove('selected');
            selectedOption.removeAttribute('aria-selected');
        }
        selectedOption = option;
        selectedOption.classList.add('selected');
        selectedOption.setAttribute('aria-selected', 'true');
        
        const prefecture = option.dataset.prefecture || '';

        searchInput.value = `【${prefecture}】${option.textContent}`;
        selectedAreaIdInput.value = option.dataset.value;
    }

    document.addEventListener('click', (e) => {
        if (!customSelectContainer.contains(e.target)) {
            closeOptionsList();
        }
    });

    function removeActiveDescendant() {
        if (activeOptionIndex > -1 && visibleOptions[activeOptionIndex]) {
            visibleOptions[activeOptionIndex].classList.remove('active');
        }
        searchInput.removeAttribute('aria-activedescendant');
    }

    function setActiveDescendant(newIndex) {
        removeActiveDescendant();
        activeOptionIndex = newIndex;
        if (activeOptionIndex > -1 && visibleOptions[activeOptionIndex]) {
            const activeOption = visibleOptions[activeOptionIndex];
            activeOption.classList.add('active');
            searchInput.setAttribute('aria-activedescendant', activeOption.id);
            activeOption.scrollIntoView({ block: 'nearest' });
        }
    }

    searchInput.addEventListener('keydown', (e) => {
        const { key } = e;
        const isListOpen = customSelectContainer.getAttribute('aria-expanded') === 'true';

        if (key === 'Escape') {
            if (isListOpen) {
                e.preventDefault();
                closeOptionsList();
            }
            return;
        }

        if (!isListOpen) {
            if (key === 'ArrowDown' || key === 'ArrowUp') {
                e.preventDefault();
                openOptionsList();
            }
            return;
        }

        if (visibleOptions.length === 0) return;

        let newIndex = activeOptionIndex;

        switch(key) {
            case 'ArrowDown':
                e.preventDefault();
                newIndex = activeOptionIndex < visibleOptions.length - 1 ? activeOptionIndex + 1 : 0;
                break;
            case 'ArrowUp':
                e.preventDefault();
                newIndex = activeOptionIndex > 0 ? activeOptionIndex - 1 : visibleOptions.length - 1;
                break;
            case 'Enter':
                if (activeOptionIndex > -1) {
                    e.preventDefault();
                    selectOption(visibleOptions[activeOptionIndex]);
                    closeOptionsList();
                }
                // Allow form submission if no option is active
                return; 
            case 'Home':
                e.preventDefault();
                newIndex = 0;
                break;
            case 'End':
                e.preventDefault();
                newIndex = visibleOptions.length - 1;
                break;
            default:
                // For other keys, let the browser handle it (e.g., typing in the input)
                return;
        }
        
        setActiveDescendant(newIndex);
    });

    const buttonText = runButton.querySelector('.button-text');
    const statusCard = document.getElementById('status-card');
    const statusTitle = document.getElementById('status-title');
    const statusDetails = document.getElementById('status-details');
    const progressBar = document.getElementById('progress-bar');
    const resultCard = document.getElementById('result-card');
    const cancelButton = document.getElementById('cancel-button');
    let eventSource = null;
    let currentJobId = null;

    scrapeForm.addEventListener('submit', (event) => {
        event.preventDefault();
        resultCard.style.display = 'none'; // Clear previous results/errors first

        if (!selectedAreaIdInput.value) {
            showResultCard(false, '入力エラー', 'エリアを選択してください。');
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
        cancelButton.style.display = 'block';
        cancelButton.disabled = false;

        const areaId = selectedAreaIdInput.value;
        
        eventSource = new EventSource(`/scrape?area_id=${areaId}`);

        eventSource.addEventListener('job_id', (e) => {
            currentJobId = e.data;
        });

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

        eventSource.addEventListener('cancelled', (e) => {
            statusCard.style.display = 'none';
            showResultCard(false, '処理が中断されました', e.data, null, null);
            resetUI();
        });

        eventSource.onerror = (e) => {
            // キャンセル処理中に発生した接続エラーは、専用ハンドラに任せるため無視する
            if (currentJobId && cancelButton.disabled) {
                console.log("SSE connection error during cancellation, likely expected. The 'cancelled' event handler will manage the UI.");
                // エラー表示の前に、念のため接続を閉じておく
                if(eventSource) eventSource.close();
                return;
            }

            let errorMessage = 'サーバーとの接続で予期せぬエラーが発生しました。';
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

    cancelButton.addEventListener('click', () => {
        if (!currentJobId) return;

        cancelButton.disabled = true;
        statusTitle.textContent = '中断処理中';
        statusDetails.textContent = 'サーバーにキャンセルを要求しました...';

        fetch('/scrape/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: currentJobId }),
        })
        .catch(err => {
            statusDetails.textContent = 'キャンセルリクエスト中にエラーが発生しました。';
            cancelButton.disabled = false; // Re-enable if fetch fails
        });
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
        cancelButton.style.display = 'none';
        currentJobId = null;
    }
}); 