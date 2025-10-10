document.addEventListener('DOMContentLoaded', () => {
    // DOM 요소 가져오기
    const userIdInput = document.getElementById('userId');
    const idCheckMsg = document.getElementById('id-check-msg');
    const passwordInput = document.getElementById('password');
    const passwordConfirmInput = document.getElementById('password-confirm');
    const majorSelect = document.getElementById('major');
    const roleSelect = document.getElementById('role');
    const signupForm = document.getElementById('signup-form');

    // ✅ 비밀번호 확인 메시지를 표시할 span 요소 추가
    const passwordCheckMsg = document.getElementById('password-check-msg');

    // 데이터 및 상태 변수
    let majorsData = [];
    let rolesData = [];
    let isIdAvailable = false;

    // 전공 데이터 가져오기
    fetch('/req/major')
        .then(response => {
            if (!response.ok) throw new Error('Network response was not ok');
            return response.json();
        })
        .then(data => {
            majorsData = data;
            data.forEach(major => {
                const option = new Option(major.majorname, major.majorId);
                majorSelect.add(option);
            });
        })
        .catch(error => console.error('Error fetching majors:', error));

    // 역할 데이터 가져오기
    fetch('/req/role')
        .then(response => {
            if (!response.ok) throw new Error('Network response was not ok');
            return response.json();
        })
        .then(data => {
            rolesData = data;
            data.forEach(role => {
                const option = new Option(role.rolename, role.roleId);
                roleSelect.add(option);
            });
        })
        .catch(error => console.error('Error fetching roles:', error));

    // 아이디 중복 확인
    userIdInput.addEventListener('blur', () => {
        const userId = userIdInput.value;
        if (!userId) {
            idCheckMsg.textContent = '';
            isIdAvailable = false;
            return;
        }
        fetch('/auth/idcheck', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: userId })
        })
            .then(response => response.json())
            .then(isDuplicate => {
                if (isDuplicate) {
                    idCheckMsg.textContent = '이미 사용 중인 아이디입니다.';
                    idCheckMsg.className = 'unavailable';
                    isIdAvailable = false;
                } else {
                    idCheckMsg.textContent = '사용 가능한 아이디입니다.';
                    idCheckMsg.className = 'available';
                    isIdAvailable = true;
                }
            })
            .catch(error => {
                console.error('ID check error:', error);
                idCheckMsg.textContent = '아이디 확인 중 오류가 발생했습니다.';
                idCheckMsg.className = 'unavailable';
                isIdAvailable = false;
            });
    });

    // ✅ 비밀번호 실시간 확인 기능 추가된 부분
    const checkPasswordMatch = () => {
        const password = passwordInput.value;
        const confirmPassword = passwordConfirmInput.value;

        if (confirmPassword === "" && password === "") {
            passwordCheckMsg.textContent = '';
            return;
        }

        if (password === confirmPassword) {
            passwordCheckMsg.textContent = '비밀번호가 일치합니다.';
            passwordCheckMsg.className = 'match';
        } else {
            passwordCheckMsg.textContent = '비밀번호가 일치하지 않습니다.';
            passwordCheckMsg.className = 'mismatch';
        }
    };

    // ✅ 두 비밀번호 필드에 이벤트 리스너 추가
    passwordInput.addEventListener('input', checkPasswordMatch);
    passwordConfirmInput.addEventListener('input', checkPasswordMatch);


    // 회원가입 폼 제출
    signupForm.addEventListener('submit', (event) => {
        event.preventDefault();

        if (!isIdAvailable) {
            alert('아이디 중복 확인을 해주세요.');
            userIdInput.focus();
            return;
        }

        if (passwordInput.value !== passwordConfirmInput.value) {
            alert('비밀번호가 일치하지 않습니다. 다시 확인해주세요.');
            passwordConfirmInput.focus();
            return;
        }

        const selectedMajorIndex = majorSelect.selectedIndex - 1;
        const selectedRoleIndex = roleSelect.selectedIndex - 1;
        const selectedMajorObject = majorsData[selectedMajorIndex];
        const selectedRoleObject = rolesData[selectedRoleIndex];
        const username = document.getElementById('username').value;

        if (!username || !selectedMajorObject || !selectedRoleObject) {
            alert('모든 정보를 입력 및 선택해주세요.');
            return;
        }

        const signupPayload = {
            userId: userIdInput.value,
            password: passwordInput.value,
            username: username,
            majorId: selectedMajorObject.id,
            roleId: selectedRoleObject.id
        };

        fetch('/auth/signup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(signupPayload),
        })
            .then(response => {
                if (!response.ok) return response.json().then(err => { throw err; });
                return response.json();
            })
            .then(data => {
                console.log('Success:', data);
                alert('회원가입이 성공적으로 완료되었습니다!');
            })
            .catch(error => {
                console.error('Error:', error);
                alert(`회원가입 중 오류가 발생했습니다: ${error.message || '서버 오류'}`);
            });
    });
});