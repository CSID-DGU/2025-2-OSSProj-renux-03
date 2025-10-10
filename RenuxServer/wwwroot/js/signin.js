document.addEventListener('DOMContentLoaded', () => {
    const signinForm = document.getElementById('signin-form');

    signinForm.addEventListener('submit', (event) => {
        event.preventDefault();

        const userId = document.getElementById('userId').value;
        const password = document.getElementById('password').value;

        if (!userId || !password) {
            alert('아이디와 비밀번호를 모두 입력해주세요.');
            return;
        }

        const signinPayload = {
            userId: userId,
            password: password
        };

        fetch('/auth/signin', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(signinPayload),
        })
            .then(response => {
                if (!response.ok) {
                    throw new Error('아이디 또는 비밀번호가 올바르지 않습니다.');
                }
                // ✅ 수정된 부분: 로그인 성공 시 '/auth' 경로로 페이지를 리디렉션합니다.
                window.location.href = '/';
            })
            .catch(error => {
                console.error('로그인 실패:', error);
                alert(error.message);
            });
    });
});