document.addEventListener('DOMContentLoaded', () => {
    // DOM 요소 가져오기
    const guestView = document.getElementById('guest-view');
    const userView = document.getElementById('user-view');
    const welcomeMessage = document.getElementById('welcome-message');
    const newChatList = document.getElementById('new-chat-list');
    const activeChatSection = document.getElementById('active-chat-section');
    const activeChatList = document.getElementById('active-chat-list');
    const loginBtn = document.getElementById('login-btn');
    const signupBtn = document.getElementById('signup-btn');
    const logoutBtn = document.getElementById('logout-btn');

    // '새로운 채팅 시작' 목록 가져오기 (/req/orgs)
    const fetchNewChatOptions = () => {
        fetch('/req/orgs')
            .then(response => response.json())
            .then(data => {
                newChatList.innerHTML = '';
                data.forEach(org => {
                    const listItem = `<li><a href="/chat/new/${org.id}">${org.major.majorname}</a></li>`;
                    newChatList.innerHTML += listItem;
                });
            })
            .catch(error => {
                console.error('Error fetching new chat options:', error);
                newChatList.innerHTML = '<li>목록을 불러올 수 없습니다.</li>';
            });
    };

    // '최근 채팅' 목록 가져오기 (/chat/active) - 로그인된 사용자 전용
    const fetchActiveChats = () => {
        fetch('/chat/active')
            .then(response => {
                if (!response.ok) throw new Error('Not logged in or failed to fetch');
                return response.json();
            })
            .then(data => {
                if (data.length > 0) {
                    activeChatList.innerHTML = '';
                    data.forEach(chat => {
                        // API 응답(ActiveChatDto)에 맞춰 title과 organization의 majorname 사용
                        const title = chat.title || chat.organization.major.majorname;
                        const listItem = `<li><a href="/chat/${chat.id}">${title}</a></li>`;
                        activeChatList.innerHTML += listItem;
                    });
                    activeChatSection.classList.remove('hidden'); // 목록이 있으면 섹션을 보여줌
                }
            })
            .catch(error => {
                console.error('Could not fetch active chats:', error);
                activeChatSection.classList.add('hidden'); // 실패 시 섹션을 숨김
            });
    };

    // 로그인 상태 확인 및 UI 설정
    const checkLoginStatus = () => {
        fetch('/auth/name')
            .then(response => {
                if (!response.ok) throw new Error('Not logged in');
                return response.json();
            })
            .then(data => { // 로그인된 상태
                welcomeMessage.textContent = `환영합니다, ${data.name}님`;
                guestView.classList.add('hidden');
                userView.classList.remove('hidden');
                fetchActiveChats(); // 로그인 확인 후 활성화된 채팅 목록 로드
            })
            .catch(error => { // 로그인되지 않은 상태
                console.log('User is not logged in.');
                guestView.classList.remove('hidden');
                userView.classList.add('hidden');
                activeChatSection.classList.add('hidden'); // 비로그인 시 활성화 채팅 섹션 숨김
            });
    };

    // 버튼 이벤트 리스너 설정
    loginBtn.addEventListener('click', () => { window.location.href = '/auth/in'; });
    signupBtn.addEventListener('click', () => { window.location.href = '/auth/up'; });
    logoutBtn.addEventListener('click', () => {
        fetch('/auth/signout', { method: 'GET' })
            .then(response => {
                if (response.ok) window.location.reload();
                else throw new Error('로그아웃 실패');
            })
            .catch(error => alert(error.message));
    });

    // 페이지 로드 시 실행
    checkLoginStatus();
    fetchNewChatOptions(); // 새로운 채팅 목록은 항상 로드
});