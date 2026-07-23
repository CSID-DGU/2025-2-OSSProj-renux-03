import { type ChangeEvent, type FormEvent, type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import { mapRoleNameToUserRole } from '../../auth/roleMapping'
import {
  finalizeStoppedAssistant,
  isAbortError,
  isStoppedAssistant,
  normalizeAssistantRuns,
  prepareRegeneration,
  readGuestChatRecords,
  resolveGuestChatRoute,
  toChatPath,
  updateGuestChatMessages,
  upsertGuestChat,
  writeGuestChatRecords,
  type ChatViewMessage,
} from '../../chat/chatState'
import { useChatStream } from '../../hooks/useChatStream'
import donggukLogo from '../../assets/images/dongguk-logo.png'
import dongddokiLogo from '../../assets/images/dongddoki-logo.png'
import ChatMarkdown from '../../components/chat/ChatMarkdown'
import CopyButton from '../../components/chat/CopyButton'
import MessageFeedback from '../../components/chat/MessageFeedback'
import RegenerateButton from '../../components/chat/RegenerateButton'
import SuggestedQuestions from '../../components/chat/SuggestedQuestions'
import SourceCards from '../../components/chat/SourceCards'
import type { Department } from '../../types/organization'
import type { ActiveChat } from '../../types/chat'
import type { AuthNameResponse, UserRole } from '../../types/auth'

type AuthStatus = 'checking' | 'authenticated' | 'guest'

// Keep this boundary in sync with the drawer media query in global.css.
const MOBILE_LAYOUT_QUERY = '(max-width: 768px)'
const CHAT_INPUT_MAX_LENGTH = 2000
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

const getFocusableElements = (container: HTMLElement | null) => {
  if (!container) return []
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true')
}

const keepFocusInside = (event: globalThis.KeyboardEvent, container: HTMLElement | null) => {
  if (event.key !== 'Tab') return
  const focusable = getFocusableElements(container)
  if (focusable.length === 0) {
    event.preventDefault()
    container?.focus()
    return
  }

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

const getFallbackLabel = (reason?: string | null) => {
  if (reason === 'date_filter_eliminated_all') return '날짜 범위 재확인'
  if (reason === 'score_below_threshold') return '근거 약함'
  if (reason === 'dataset_unavailable') return '일시적 조회 실패'
  return '근거 부족'
}

const HomePage = () => {
  const navigate = useNavigate()
  const { chatId: routeChatId } = useParams<{ chatId: string }>()
  const [authStatus, setAuthStatus] = useState<AuthStatus>('checking')
  const [userName, setUserName] = useState<string | null>(null)
  const [departments, setDepartments] = useState<Department[]>([])
  const [departmentsLoading, setDepartmentsLoading] = useState(true)
  const [activeChats, setActiveChats] = useState<ActiveChat[]>([])
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [selectedDepartmentId, setSelectedDepartmentId] = useState('')
  const [chatRoomTitle, setChatRoomTitle] = useState('')
  const [isCreatingChat, setIsCreatingChat] = useState(false)
  const [createChatError, setCreateChatError] = useState<string | null>(null)
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)
  const [selectedChatTitle, setSelectedChatTitle] = useState<string | null>(null)
  const [chatMessages, setChatMessages] = useState<ChatViewMessage[]>([])
  const [chatLoading, setChatLoading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [unknownGuestChatId, setUnknownGuestChatId] = useState<string | null>(null)
  const [hasMoreMessages, setHasMoreMessages] = useState(true)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatSending, setChatSending] = useState(false)
  const [activeCitation, setActiveCitation] = useState<{ messageId: string; citationNumber: number } | null>(null)
  const [userRole, setUserRole] = useState<UserRole>(() => {
    if (typeof window === 'undefined') return 'STUDENT'
    const stored = window.localStorage.getItem('renux-user-role')
    if (stored === 'DEPARTMENT_COUNCIL' || stored === 'UNIVERSITY_COUNCIL') return stored
    return 'STUDENT'
  })
  const [departmentName, setDepartmentName] = useState<string | null>(null)
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null)
  const messagesEndRef = useRef<HTMLLIElement>(null)
  const skipLoadOnSelectRef = useRef<string | null>(null)
  const isLoadingMoreRef = useRef(false)
  const sidebarRef = useRef<HTMLElement | null>(null)
  const mobileMenuButtonRef = useRef<HTMLButtonElement | null>(null)
  const modalRef = useRef<HTMLDivElement | null>(null)
  const modalReturnFocusRef = useRef<HTMLElement | null>(null)
  const departmentSelectRef = useRef<HTMLSelectElement | null>(null)
  const { streamMessage, stopStream } = useChatStream()
  const isAuthenticated = authStatus === 'authenticated'

  // Mobile sidebar state
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isMobileLayout, setIsMobileLayout] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(MOBILE_LAYOUT_QUERY).matches,
  )

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  // Removed useEffect for auto-scrolling to bottom on chatMessages change
  // to prevent scrolling to bottom when loading older messages.
  
  const closeSidebar = useCallback((restoreFocus = true) => {
    setIsSidebarOpen(false)
    if (restoreFocus) {
      window.requestAnimationFrame(() => mobileMenuButtonRef.current?.focus())
    }
  }, [])

  const openSidebar = () => {
    setIsSidebarOpen(true)
    window.requestAnimationFrame(() => getFocusableElements(sidebarRef.current)[0]?.focus())
  }

  useEffect(() => {
    const mediaQuery = window.matchMedia(MOBILE_LAYOUT_QUERY)
    const updateLayout = () => setIsMobileLayout(mediaQuery.matches)
    updateLayout()
    mediaQuery.addEventListener('change', updateLayout)
    return () => mediaQuery.removeEventListener('change', updateLayout)
  }, [])

  useEffect(() => {
    if (!isMobileLayout) setIsSidebarOpen(false)
  }, [isMobileLayout])

  useEffect(() => {
    if (!isMobileLayout || !isSidebarOpen || isModalOpen) return

    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeSidebar()
        return
      }
      keepFocusInside(event, sidebarRef.current)
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [closeSidebar, isMobileLayout, isModalOpen, isSidebarOpen])

  // Close sidebar when switching chats on mobile.
  useEffect(() => {
    setIsSidebarOpen(false)
  }, [selectedChatId])

  useEffect(() => () => stopStream(), [routeChatId, stopStream])

  const isNewChatDisabled = useMemo(() => {
    if (departmentsLoading) return true
    return departments.length === 0
  }, [departments, departmentsLoading])

  useEffect(() => {
    const loadDepartments = async () => {
      setDepartmentsLoading(true)
      try {
        const data = await apiFetch<Department[]>('/req/orgs', { method: 'GET' })
        if (Array.isArray(data)) {
          setDepartments(data)
        } else {
          setDepartments([])
        }
      } catch (error) {
        console.error('Failed to load departments; switching to demo data', error)
        setDepartments([])
      } finally {
        setDepartmentsLoading(false)
      }
    }
    loadDepartments()
  }, [])

  useEffect(() => {
    const checkLoginStatus = async () => {
      try {
        const data = await apiFetch<AuthNameResponse>('/auth/name', { method: 'GET' })
        if (data?.name) {
          setAuthStatus('authenticated')
          setUserName(data.name)
          
          const rawRole = data.roleName || data.role
          if (rawRole) {
            const resolvedRole = mapRoleNameToUserRole(rawRole)
            setUserRole(resolvedRole)
            if (typeof window !== 'undefined') {
              window.localStorage.setItem('renux-user-role', resolvedRole)
            }
          }
          if (data.majorName) {
            setDepartmentName(data.majorName)
          }
          return
        }
        setAuthStatus('guest')
      } catch (error) {
        console.log('User is not logged in', error)
        setAuthStatus('guest')
        setUserName(null)
        setDepartmentName(null)
        setUserRole('STUDENT')
        window.localStorage.removeItem('renux-user-role')
      }
    }
    checkLoginStatus()
  }, [])

  useEffect(() => {
    const fetchActiveChats = async () => {
      if (authStatus === 'checking') return

      if (authStatus === 'authenticated') {
        try {
          const data = await apiFetch<ActiveChat[]>('/chat/active', { method: 'GET' })
          if (Array.isArray(data)) {
            setActiveChats(data)
          }
        } catch (error) {
          console.error('Failed to load active chats', error)
          setActiveChats([])
        }
      } else {
        setActiveChats(readGuestChatRecords(window.localStorage))
      }
    }
    fetchActiveChats()
  }, [authStatus])

  useEffect(() => {
    document.body.classList.toggle('modal-open', isModalOpen)
    return () => {
      document.body.classList.remove('modal-open')
    }
  }, [isModalOpen])

  const resetNewChatForm = useCallback(() => {
    setCreateChatError(null)
    setSelectedDepartmentId('')
    setChatRoomTitle('')
  }, [])

  const handleNewChatClick = () => {
    modalReturnFocusRef.current = isMobileLayout
      ? mobileMenuButtonRef.current
      : document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
    if (isMobileLayout) closeSidebar(false)
    resetNewChatForm()
    setIsModalOpen(true)
  }

  const handleModalClose = useCallback((restoreFocus = true) => {
    setIsModalOpen(false)
    resetNewChatForm()
    if (restoreFocus) {
      window.requestAnimationFrame(() => modalReturnFocusRef.current?.focus())
    }
  }, [resetNewChatForm])

  useEffect(() => {
    if (!isModalOpen) return

    window.requestAnimationFrame(() => departmentSelectRef.current?.focus())
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && !isCreatingChat) {
        event.preventDefault()
        handleModalClose()
        return
      }
      keepFocusInside(event, modalRef.current)
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleModalClose, isCreatingChat, isModalOpen])

  const storeGuestChat = useCallback((chat: ActiveChat) => {
    const updated = upsertGuestChat(readGuestChatRecords(window.localStorage), chat)
    writeGuestChatRecords(window.localStorage, updated)
    setActiveChats(updated)
  }, [])

  const resizeChatInput = useCallback(() => {
    window.requestAnimationFrame(() => {
      const textarea = chatInputRef.current
      if (!textarea) return
      textarea.style.height = 'auto'
      textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`
    })
  }, [])

  const handleChatInputChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setChatInput(event.target.value.slice(0, CHAT_INPUT_MAX_LENGTH))
    resizeChatInput()
  }

  const handleCreateChat = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setCreateChatError(null)

    if (!selectedDepartmentId) {
      setCreateChatError('학과를 먼저 선택해주세요.')
      return
    }

    const trimmedTitle = chatRoomTitle.trim()
    if (!trimmedTitle) {
      setCreateChatError('채팅방 제목을 입력해주세요.')
      return
    }

    const selectedOrg = departments.find((dept) => dept.id === selectedDepartmentId)
    if (!selectedOrg || !selectedOrg.major?.id) {
      setCreateChatError('선택한 학과 정보를 불러오지 못했습니다.')
      return
    }

    try {
      setIsCreatingChat(true)
      const chatRoom = await apiFetch<ActiveChat>('/chat/start', {
        method: 'POST',
        json: { org: selectedOrg, title: trimmedTitle },
      })
      if (authStatus === 'guest') {
        storeGuestChat(chatRoom)
      } else {
        setActiveChats((prev) => [chatRoom, ...prev])
      }
      
      handleModalClose(false)
      setChatMessages([])
      setChatError(null)
      navigate(toChatPath(chatRoom.id))
      window.requestAnimationFrame(() => {
        resizeChatInput()
        chatInputRef.current?.focus()
      })
    } catch (error) {
      console.error('Failed to create chat room', error)
      setCreateChatError('채팅방을 생성하지 못했습니다. 잠시 후 다시 시도해주세요.')
    } finally {
      setIsCreatingChat(false)
    }
  }

  const handleLogin = () => {
    navigate('/auth/in')
  }

  const handleSignup = () => {
    navigate('/auth/up')
  }

  const handleLogout = async () => {
    try {
      await apiFetch('/auth/signout', { method: 'POST' })
      // 이전 세션의 역할이 다음 사용자에게 노출되지 않도록 캐시 제거
      window.localStorage.removeItem('renux-user-role')
      window.location.reload()
    } catch (error) {
      console.error('Failed to logout', error)
      // alert() 대신 인라인 표시 — 다른 에러 처리 패턴과 통일
      setChatError('로그아웃에 실패했습니다. 다시 시도해주세요.')
    }
  }

  const handleOpenUniversityAdmin = () => {
    navigate('/admin/university')
  }

  const handleOpenDepartmentAdmin = () => {
    navigate('/admin/department')
  }

  const handleOpenSettings = () => {
    navigate('/settings')
  }

  const handleSelectChat = (chat: ActiveChat) => {
    if (chatSending) return
    if (isMobileLayout) {
      closeSidebar(false)
      window.requestAnimationFrame(() => chatInputRef.current?.focus())
    }
    navigate(toChatPath(chat.id))
  }

  const loadMessages = useCallback(async (chatIdToLoad: string) => {
    try {
      setChatLoading(true)
      setChatError(null)
      setHasMoreMessages(true)
      const data = await apiFetch<ChatViewMessage[]>('/chat/load', {
        method: 'POST',
        json: { chatId: chatIdToLoad, lastTime: new Date().toISOString() },
      })
      if (Array.isArray(data)) {
        setChatMessages(normalizeAssistantRuns([...data].reverse()))
        if (data.length < 20) setHasMoreMessages(false)
        setTimeout(scrollToBottom, 100) 
      } else {
        setChatMessages([])
        setHasMoreMessages(false)
      }
    } catch (err) {
      console.error('Failed to load messages', err)
      setChatError('채팅 메시지를 불러오지 못했습니다.')
      setChatMessages([])
    } finally {
      setChatLoading(false)
    }
  }, [scrollToBottom])

  const loadMoreMessages = async () => {
    if (!isAuthenticated || !selectedChatId || isLoadingMoreRef.current || !hasMoreMessages || chatMessages.length === 0) return

    const firstMessageTime = chatMessages[0].createdTime
    const container = document.querySelector('.home-chat__thread-wrapper') as HTMLDivElement
    const prevScrollHeight = container?.scrollHeight ?? 0

    try {
      setIsLoadingMore(true)
      isLoadingMoreRef.current = true
      
      const data = await apiFetch<ChatViewMessage[]>('/chat/load', {
        method: 'POST',
        json: { chatId: selectedChatId, lastTime: firstMessageTime },
      })
      if (Array.isArray(data) && data.length > 0) {
        const newMessages = [...data].reverse()
        setChatMessages((prev) => normalizeAssistantRuns([...newMessages, ...prev]))
        
        setTimeout(() => {
            if (container) {
                container.scrollTop = container.scrollHeight - prevScrollHeight
            }
        }, 0)

        if (data.length < 20) setHasMoreMessages(false)
      } else {
        setHasMoreMessages(false)
      }
    } catch (err) {
      console.error('Failed to load older messages', err)
    } finally {
      setIsLoadingMore(false)
      // 약간의 지연을 두어 상태 업데이트가 완료된 후 플래그를 해제 (안전장치)
      setTimeout(() => {
        isLoadingMoreRef.current = false
      }, 100)
    }
  }

  useEffect(() => {
    if (authStatus === 'checking') return

    if (!routeChatId) {
      setSelectedChatId(null)
      setSelectedChatTitle(null)
      setChatMessages([])
      setChatError(null)
      setUnknownGuestChatId(null)
      setHasMoreMessages(false)
      return
    }

    if (skipLoadOnSelectRef.current === routeChatId) {
      skipLoadOnSelectRef.current = null
      setUnknownGuestChatId(null)
      return
    }

    if (authStatus === 'guest') {
      const records = readGuestChatRecords(window.localStorage)
      const route = resolveGuestChatRoute(routeChatId, records)
      setActiveChats(records)
      setHasMoreMessages(false)
      setChatLoading(false)
      setChatError(null)

      if (route.kind === 'unknown') {
        setSelectedChatId(null)
        setSelectedChatTitle(null)
        setChatMessages([])
        setUnknownGuestChatId(route.chatId)
        return
      }

      if (route.kind === 'known') {
        setSelectedChatId(route.chat.id)
        setSelectedChatTitle(route.chat.title ?? '채팅방')
        setChatMessages(normalizeAssistantRuns(route.chat.messages ?? []))
        setUnknownGuestChatId(null)
        setTimeout(scrollToBottom, 0)
      }
      return
    }

    setSelectedChatId(routeChatId)
    setSelectedChatTitle('채팅방')
    setUnknownGuestChatId(null)
    loadMessages(routeChatId)
  }, [authStatus, loadMessages, routeChatId, scrollToBottom])

  useEffect(() => {
    if (!selectedChatId) return
    const selected = activeChats.find((chat) => chat.id === selectedChatId)
    if (selected?.title) setSelectedChatTitle(selected.title)
  }, [activeChats, selectedChatId])

  useEffect(() => {
    if (
      authStatus !== 'guest'
      || !selectedChatId
      || unknownGuestChatId
      || chatLoading
      || chatSending
    ) return

    const updated = updateGuestChatMessages(
      readGuestChatRecords(window.localStorage),
      selectedChatId,
      chatMessages,
    )
    writeGuestChatRecords(window.localStorage, updated)
    setActiveChats(updated)
  }, [authStatus, chatLoading, chatMessages, chatSending, selectedChatId, unknownGuestChatId])

  const streamIntoAssistant = async (
    question: ChatViewMessage,
    assistantId: string,
    restoreInputOnError: boolean,
  ) => {
    setChatSending(true)
    setChatError(null)
    setTimeout(scrollToBottom, 0)

    try {
      const { receivedAny } = await streamMessage(
        {
          id: question.id,
          chatId: question.chatId,
          content: question.content,
          createdTime: typeof question.createdTime === 'string'
            ? question.createdTime
            : new Date().toISOString(),
        },
        {
          onText: (accumulated) => {
            setChatMessages((prev) =>
              prev.map((msg) => (msg.id === assistantId ? { ...msg, content: accumulated } : msg)),
            )
            setTimeout(scrollToBottom, 0)
          },
          onMetadata: (meta) =>
            setChatMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? {
                      ...msg,
                      sources: meta.sources,
                      requestId: meta.requestId,
                      isFallback: meta.isFallback,
                      fallbackReason: meta.fallbackReason,
                    }
                  : msg,
              ),
            ),
          onSuggestions: (questions) =>
            setChatMessages((prev) =>
              prev.map((msg) => (msg.id === assistantId ? { ...msg, suggestedQuestions: questions } : msg)),
            ),
          onGrounding: ({ grounded, groundingScore }) =>
            setChatMessages((prev) =>
              prev.map((msg) => (msg.id === assistantId ? { ...msg, grounded, groundingScore } : msg)),
            ),
        },
      )

      if (!receivedAny) {
        setChatMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? { ...msg, content: '응답을 받지 못했습니다. 다시 생성해주세요.', isFallback: true }
              : msg,
          ),
        )
      }
      setTimeout(scrollToBottom, 100)
    } catch (err) {
      if (isAbortError(err)) {
        setChatError(null)
        setChatMessages((prev) => finalizeStoppedAssistant(prev, assistantId))
      } else {
        console.error('Failed to send message', err)
        setChatError('메시지를 전송하지 못했습니다.')
        setChatMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? { ...msg, content: '응답 연결이 끊겼습니다. 재생성 버튼으로 다시 시도해주세요.', isFallback: true }
              : msg,
          ),
        )
        if (restoreInputOnError) {
          setChatInput(question.content)
          resizeChatInput()
        }
      }
    } finally {
      setChatSending(false)
    }
  }

  const sendChatMessage = async (text: string, chatId: string | number) => {
    const resolvedChatId = String(chatId)
    const newMsg: ChatViewMessage = {
      id: typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      chatId: resolvedChatId,
      isAsk: true,
      content: text,
      createdTime: new Date().toISOString(),
    }

    // 스트리밍 토큰을 채워 넣을 빈 봇 말풍선을 미리 추가(내용이 비면 타이핑 인디케이터로 렌더)
    const botMessageId = typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `bot-${Date.now()}`
    const botPlaceholder: ChatViewMessage = {
      id: botMessageId,
      chatId: resolvedChatId,
      isAsk: false,
      content: '',
      createdTime: new Date().toISOString(),
      sources: [],
    }

    // 내 메시지 + 빈 봇 말풍선을 함께 추가
    setChatMessages((prev) => [...prev, newMsg, botPlaceholder])
    await streamIntoAssistant(newMsg, botMessageId, true)
  }

  const regenerateChatMessage = async (assistantId: string) => {
    if (chatSending) return
    const prepared = prepareRegeneration(chatMessages, assistantId)
    if (!prepared) {
      setChatError('재생성할 질문을 찾지 못했습니다.')
      return
    }

    setChatMessages(prepared.messages)
    await streamIntoAssistant(prepared.question, prepared.assistant.id, false)
  }

  const handleChatSubmit = async (event: FormEvent<HTMLFormElement> | KeyboardEvent<HTMLTextAreaElement>) => {
    event.preventDefault()
    
    const trimmed = chatInput.trim()
    if (!trimmed) {
      setChatError('메시지를 입력해주세요.')
      return
    }
    if (trimmed.length > CHAT_INPUT_MAX_LENGTH) {
      setChatError(`메시지는 ${CHAT_INPUT_MAX_LENGTH.toLocaleString()}자까지 입력할 수 있습니다.`)
      return
    }

    let currentChatId = selectedChatId

    // 채팅방이 없으면 자동 생성
    if (!currentChatId) {
      if (departments.length === 0) {
        setChatError('채팅을 시작할 수 있는 학과 정보가 없습니다.')
        return
      }

      try {
        setChatSending(true)
        const defaultOrg = departments[0]
        const title = trimmed.length > 20 ? trimmed.substring(0, 20) + '...' : trimmed
        
        const chatRoom = await apiFetch<ActiveChat>('/chat/start', {
          method: 'POST',
          json: { org: defaultOrg, title },
        })

        if (authStatus === 'guest') {
          storeGuestChat(chatRoom)
        } else {
          setActiveChats((prev) => [chatRoom, ...prev])
        }

        currentChatId = chatRoom.id
        // URL effect가 방금 준비한 메시지 상태를 다시 덮어쓰지 않게 한 번만 건너뛴다.
        skipLoadOnSelectRef.current = chatRoom.id
        setSelectedChatId(chatRoom.id)
        setSelectedChatTitle(chatRoom.title ?? title)
        setUnknownGuestChatId(null)
        navigate(toChatPath(chatRoom.id))

        // 방금 생성된 방의 환영 메시지를 수동으로 가져옴
        const initialData = await apiFetch<ChatViewMessage[]>('/chat/load', {
          method: 'POST',
          json: { chatId: chatRoom.id, lastTime: new Date().toISOString() },
        })
        
        // 환영 메시지 설정 (있다면)
        if (Array.isArray(initialData)) {
           setChatMessages(normalizeAssistantRuns([...initialData].reverse()))
        } else {
           setChatMessages([])
        }

      } catch (error) {
        console.error('Failed to auto-create chat room', error)
        setChatError('채팅방을 생성하지 못했습니다.')
        setChatSending(false)
        return
      }
    }

    setChatInput('')
    resizeChatInput()
    await sendChatMessage(trimmed, currentChatId)
  }

  const isHeroPrimaryDisabled = isNewChatDisabled
  const displayName = isAuthenticated ? userName ?? '로그인 사용자' : '게스트'
  const displayDept = isAuthenticated
    ? departmentName ?? (userRole === 'UNIVERSITY_COUNCIL' ? '총학생회' : '동국대학교')
    : '동국대학교'
  const roleLabelMap: Record<UserRole, string> = {
    STUDENT: '일반학생',
    DEPARTMENT_COUNCIL: '학생회',
    UNIVERSITY_COUNCIL: '총학생회',
  }
  const roleLabel = roleLabelMap[userRole] // '일반학생'
  const showDeptAdminButton = isAuthenticated && userRole === 'DEPARTMENT_COUNCIL' // '학생회'
  const showUnivAdminButton = isAuthenticated && userRole === 'UNIVERSITY_COUNCIL' // '총학생회'
  const showRagScores = isAuthenticated && userRole === 'UNIVERSITY_COUNCIL'
  const visibleChats = activeChats.length > 0 ? activeChats : [] 

  const formatMessageTime = (value?: string | number) => {
    if (!value) return ''
    const date = typeof value === 'number' ? new Date(value) : new Date(value)
    if (Number.isNaN(date.getTime())) return ''
    return new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' }).format(date)
  }

  const starterQuestions = useMemo(() => {
    const dept = isAuthenticated ? (departmentName ?? '').trim() : ''
    const questions: string[] = []

    if (dept) {
      questions.push(`${dept} 전공필수 과목 알려줘`)
      questions.push(`${dept} 사무실 연락처 알려줘`)
    }

    if (isAuthenticated && userRole === 'DEPARTMENT_COUNCIL') {
      questions.push('최근 학과 관련 공지 보여줘')
      questions.push('이번 달 학사일정 알려줘')
    } else if (isAuthenticated && userRole === 'UNIVERSITY_COUNCIL') {
      questions.push('오늘 올라온 공지 요약해줘')
      questions.push('최근 장학 공지 보여줘')
    } else {
      questions.push('최근 장학 공지 보여줘')
      questions.push('이번 달 학사일정 알려줘')
      questions.push('오늘 학식 뭐 나와?')
    }

    return Array.from(new Set(questions)).slice(0, 6)
  }, [departmentName, isAuthenticated, userRole])

  const handleStarterQuestionSelect = (question: string) => {
    setChatInput(question)
    window.requestAnimationFrame(() => {
      resizeChatInput()
      chatInputRef.current?.focus()
    })
  }

  return (
    <div className="gpt-home">
      {/* Mobile Backdrop */}
      <div 
        className={`mobile-backdrop ${isSidebarOpen ? 'open' : ''}`} 
        onClick={() => closeSidebar()}
        aria-hidden="true"
      />

      <aside
        ref={sidebarRef}
        id="chat-navigation-drawer"
        className={`gpt-home__sidebar ${isSidebarOpen ? 'mobile-open' : ''}`}
        role={isMobileLayout ? 'dialog' : undefined}
        aria-label="채팅 목록 및 계정"
        aria-modal={isMobileLayout && isSidebarOpen ? true : undefined}
        aria-hidden={isMobileLayout && !isSidebarOpen ? true : undefined}
        inert={isMobileLayout && !isSidebarOpen}
        tabIndex={isMobileLayout ? -1 : undefined}
      >
        <div className="gpt-home__brand">
          <div className="home-logo-row">
            <img src={donggukLogo} alt="Dongguk University" className="home-logo home-logo--univ" />
          </div>

        </div>

        <button type="button" className="gpt-home__new" onClick={handleNewChatClick} disabled={isHeroPrimaryDisabled || chatSending}>
          + 새 대화
        </button>

        <div className="gpt-home__section gpt-home__chat-section">
          <div className="gpt-home__section-head">
            <h3>최근 대화</h3>
          </div>
          <ul className="gpt-home__chat-list">
            {visibleChats.map((chat) => (
              <li key={chat.id}>
                <button
                  type="button"
                  className="gpt-home__chat-item"
                  onClick={() => handleSelectChat(chat)}
                  disabled={chatSending}
                  aria-current={selectedChatId === chat.id ? 'page' : undefined}
                >
                  <span className="gpt-home__chat-title">{chat.title ?? '제목 없음'}</span>
                  <span className="gpt-home__chat-sub">대화 이어가기</span>
                </button>
              </li>
            ))}
            {visibleChats.length === 0 && (
              <li className="gpt-home__chat-empty">아직 대화가 없습니다. 새 대화를 시작해보세요.</li>
            )}
          </ul>
        </div>

        {/* Mobile Sidebar Footer (Account Actions) */}
        <div className="gpt-home__sidebar-footer mobile-only">
            <div className="gpt-home__section">
                <div className="gpt-home__section-head">
                    <h3>내 계정</h3>
                    {isAuthenticated && <span className="gpt-home__pill">{roleLabel}</span>}
                </div>
                <div className="gpt-home__actions">
                    {isAuthenticated ? (
                        <>
                            {showUnivAdminButton && (
                                <button className="ghost-btn small ghost-btn--accent" type="button" onClick={handleOpenUniversityAdmin}>
                                    관리자
                                </button>
                            )}
                            {showDeptAdminButton && (
                                <button className="ghost-btn small" type="button" onClick={handleOpenDepartmentAdmin}>
                                    학과 관리자
                                </button>
                            )}
                            <button className="ghost-btn small" type="button" onClick={handleOpenSettings}>
                                알림 설정
                            </button>
                            <button className="ghost-btn small" type="button" onClick={handleLogout}>
                                로그아웃
                            </button>
                        </>
                    ) : (
                        <>
                            <button className="ghost-btn small" type="button" onClick={handleLogin}>
                                로그인
                            </button>
                            <button className="ghost-btn small ghost-btn--accent" type="button" onClick={handleSignup}>
                                회원가입
                            </button>
                        </>
                    )}
                </div>
            </div>
        </div>
      </aside>

      <div className="buddy-topbar">
          <div className="buddy-topbar__brand">
            <button 
              ref={mobileMenuButtonRef}
              type="button" 
              className="mobile-menu-btn"
              onClick={openSidebar}
              aria-label="채팅 목록 열기"
              aria-expanded={isSidebarOpen}
              aria-controls="chat-navigation-drawer"
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="3" y1="12" x2="21" y2="12"></line>
                <line x1="3" y1="6" x2="21" y2="6"></line>
                <line x1="3" y1="18" x2="21" y2="18"></line>
              </svg>
            </button>
            <div className="buddy-topbar__icon buddy-topbar__icon--image">
              <img src={dongddokiLogo} alt="동똑이 로고" className="buddy-topbar__logo" />
            </div>
            <div>
              {/* <p className="buddy-topbar__eyebrow">DONGGUK BUDDY AI</p> */}
              <p className="buddy-topbar__title">동국대학교 동똑이</p>
            </div>
          </div>
          <div className="buddy-topbar__meta">
            {isAuthenticated ? (
              <>
                <span className="buddy-topbar__text">{displayName}</span>
                {displayDept && (
                  <>
                    <span className="buddy-topbar__dot">·</span>
                    <span className="buddy-topbar__text buddy-topbar__text--muted">{displayDept}</span>
                  </>
                )}
                <span className="buddy-topbar__badge">{roleLabel}</span>
              </>
            ) : (
              <span className="buddy-topbar__text buddy-topbar__text--muted">로그인 또는 회원가입 후 이용할 수 있습니다</span>
            )}
          </div>
          <div className="buddy-topbar__meta buddy-topbar__meta--actions">
            {showUnivAdminButton && (
              <button className="ghost-btn small ghost-btn--accent" type="button" onClick={handleOpenUniversityAdmin}>
                관리자
              </button>
            )}
            {showDeptAdminButton && (
              <button className="ghost-btn small" type="button" onClick={handleOpenDepartmentAdmin}>
                학과 관리자
              </button>
            )}
            {isAuthenticated ? (
              <>
                <button className="ghost-btn small" type="button" onClick={handleOpenSettings}>
                  알림 설정
                </button>
                <button className="ghost-btn small" type="button" onClick={handleLogout}>
                  로그아웃
                </button>
              </>
            ) : (
              <>
                <button className="ghost-btn small" type="button" onClick={handleLogin}>
                  로그인
                </button>
                <button className="ghost-btn small ghost-btn--accent" type="button" onClick={handleSignup}>
                  회원가입
                </button>
              </>
            )}
          </div>
        </div>

        <main className="gpt-home__main gpt-home__main--chat">
          <section className="home-chat glass-panel home-chat--fullheight">
            <div className="home-chat__header">
              <div>
                <p className="chatbot-hero__badge">동국대학교 재학생 맞춤형 정보 제공 챗봇</p>
                {selectedChatTitle && <h2 className="home-chat__title">{selectedChatTitle}</h2>}
              </div>
            </div>

            <div
              className="home-chat__thread-wrapper"
              role="region"
              aria-label="대화 내용"
              aria-busy={chatLoading || chatSending}
              onScroll={(e) => {
                const target = e.currentTarget
                // 정확히 0이 아닌 근접 임계값 — 관성 스크롤로 0을 스치지 못해도 로드되도록
                if (target.scrollTop <= 16 && hasMoreMessages && !isLoadingMore) {
                  loadMoreMessages()
                }
              }}
            >
              {chatLoading ? (
                <p className="home-chat__status">채팅을 불러오는 중...</p>
              ) : unknownGuestChatId ? (
                <div className="home-guide">
                  <h3>이 기기에 저장된 대화가 아닙니다</h3>
                  <p>게스트 대화는 대화를 시작한 기기의 브라우저에만 저장됩니다.</p>
                  <button
                    type="button"
                    className="hero-btn hero-btn--primary"
                    onClick={() => navigate('/')}
                  >
                    새 대화 시작
                  </button>
                </div>
              ) : !selectedChatId ? (
                <div className="home-guide">
                  <div className="home-guide__starters">
                    <div className="home-guide__context">
                      <span>{displayDept ?? '동국대학교'}</span>
                      <strong>{isAuthenticated ? `${roleLabel} 맞춤 질문` : '바로 물어볼 질문'}</strong>
                    </div>
                    <div className="suggested-questions" aria-label="추천 질문">
                      <div className="suggested-questions__heading">추천 질문</div>
                      <div className="suggested-questions__list">
                        {starterQuestions.map((question) => (
                          <button
                            key={question}
                            type="button"
                            className="suggested-questions__chip"
                            disabled={chatSending}
                            onClick={() => handleStarterQuestionSelect(question)}
                          >
                            {question}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                  <h3>동똑이 사용 가이드</h3>
                  <ol>
                    <li>
                      <strong>새 대화 시작하기</strong>
                      <p>좌측 사이드바의 <em>+ 새 대화</em> 버튼을 클릭하여 채팅방을 생성하세요.</p>
                    </li>
                    <li>
                      <strong>질문하기</strong>
                      <p>학사 일정, 장학금, 규정 등 궁금한 내용을 자유롭게 질문하세요.</p>
                    </li>
                    <li>
                      <strong>로그인 기능</strong>
                      <p>회원가입 후 로그인하면 대화 내역이 저장되고, 소속 학과에 맞는 맞춤형 답변을 받을 수 있습니다.</p>
                    </li>
                  </ol>
                </div>
              ) : chatMessages.length === 0 ? (
                <div className="home-chat__empty">아직 메시지가 없습니다. 첫 메시지를 보내보세요.</div>
              ) : (
                <ul className="chat-bubbles" role="log" aria-live="polite" aria-busy={chatSending} aria-label="채팅 메시지">
                  {isLoadingMore && <li className="home-chat__status"><small>이전 대화 불러오는 중...</small></li>}

                  {chatMessages.map((message, index) => {
                    const messageTime = formatMessageTime(message.createdTime)
                    // 스트리밍 대기 중인 빈 봇 말풍선은 타이핑 인디케이터로 렌더
                    const isStreamingPlaceholder = !message.isAsk && !message.content
                    const isStoppedAttempt = isStoppedAssistant(message)
                    const previousUserMessage = !message.isAsk
                      ? [...chatMessages.slice(0, index)].reverse().find((candidate) => candidate.isAsk && candidate.content.trim().length > 0)
                      : null
                    return (
                      <li
                        key={message.id}
                        className={`chat-bubble ${message.isAsk ? 'chat-bubble--user' : 'chat-bubble--bot'} ${!message.isAsk && message.isFallback ? 'chat-bubble--fallback' : ''}`}
                      >
                        {isStreamingPlaceholder ? (
                          <div className="typing-indicator" role="status" aria-live="polite">
                            <span className="visually-hidden">동똑이가 답변을 작성 중입니다.</span>
                            <div className="typing-indicator__dots" aria-hidden="true">
                              <span className="typing-dot"></span>
                              <span className="typing-dot"></span>
                              <span className="typing-dot"></span>
                            </div>
                          </div>
                        ) : (
                          <>
                            {isStoppedAttempt && (
                              <span className="chat-fallback-badge" role="status">
                                사용자가 생성을 중단한 임시 답변입니다.
                              </span>
                            )}
                            {!message.isAsk && message.isFallback && <span className="chat-fallback-badge">{getFallbackLabel(message.fallbackReason)}</span>}
                            <ChatMarkdown
                              content={message.content}
                              onCitationClick={(citationNumber) => setActiveCitation({ messageId: message.id, citationNumber })}
                            />
                            {!message.isAsk && !isStoppedAttempt && message.grounded === false && (
                              <span
                                className="chat-fallback-badge"
                                title={typeof message.groundingScore === 'number' ? `근거 일치도 약 ${Math.round(message.groundingScore * 100)}%` : undefined}
                              >
                                ⚠️ 제공된 자료로 충분히 확인되지 않은 내용이 포함될 수 있어요.
                              </span>
                            )}
                            {!message.isAsk && !isStoppedAttempt && (
                              <SourceCards
                                sources={message.sources}
                                showScores={showRagScores}
                                isFallback={message.isFallback}
                                activeCitationNumber={activeCitation?.messageId === message.id ? activeCitation.citationNumber : null}
                              />
                            )}
                            {!message.isAsk && !isStoppedAttempt && message.content.trim().length > 0 && <CopyButton text={message.content} />}
                            {!message.isAsk && !isStoppedAttempt && message.content.trim().length > 0 && previousUserMessage && selectedChatId && (
                              <RegenerateButton
                                disabled={chatSending}
                                onRegenerate={() => regenerateChatMessage(message.id)}
                              />
                            )}
                            {!message.isAsk && !isStoppedAttempt && message.requestId && (
                              <MessageFeedback requestId={message.requestId} disabled={chatSending} />
                            )}
                            {!message.isAsk && !isStoppedAttempt && (
                              <SuggestedQuestions
                                questions={message.suggestedQuestions ?? []}
                                requestId={message.requestId}
                                disabled={chatSending}
                                onSelect={(question) => {
                                  if (selectedChatId) {
                                    sendChatMessage(question, selectedChatId)
                                  }
                                }}
                              />
                            )}
                            {messageTime && <time className="chat-bubble__time">{messageTime}</time>}
                          </>
                        )}
                      </li>
                    )
                  })}
                  <li className="chat-bubbles__end" ref={messagesEndRef} aria-hidden="true" />
                </ul>
              )}
            </div>

            <form className="home-chat__composer" onSubmit={handleChatSubmit} aria-busy={chatSending}>
              <div className="home-chat__input-wrapper">
                <textarea
                  ref={chatInputRef}
                  aria-label="채팅 메시지"
                  aria-describedby="chat-input-contract"
                  aria-invalid={chatError ? true : undefined}
                  className="home-chat__input"
                  placeholder={selectedChatId ? '무엇이든 물어보세요' : '무엇이든 물어보세요 (새 대화가 자동으로 시작됩니다)'}
                  value={chatInput}
                  onChange={handleChatInputChange}
                  onKeyDown={(event) => {
                    // isComposing: 한글 조합 중 Enter가 글자 확정+전송으로 이중 동작하는 것 방지
                    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                      event.preventDefault()
                      handleChatSubmit(event)
                    }
                  }}
                  rows={1}
                  maxLength={CHAT_INPUT_MAX_LENGTH}
                  disabled={chatSending || Boolean(unknownGuestChatId) || (departmentsLoading && !selectedChatId)}
                />
                {chatSending ? (
                  <button
                    className="hero-btn hero-btn--primary home-chat__send-btn"
                    type="button"
                    onClick={stopStream}
                    aria-label="답변 생성 중단"
                  >
                    중단
                  </button>
                ) : (
                  <button
                    className="hero-btn hero-btn--primary home-chat__send-btn"
                    type="submit"
                    disabled={Boolean(unknownGuestChatId) || (departmentsLoading && !selectedChatId)}
                    aria-label="메시지 보내기"
                  >
                    보내기
                  </button>
                )}
              </div>
              <div className="home-chat__composer-meta" id="chat-input-contract">
                <span>Enter로 전송 · Shift+Enter로 줄바꿈</span>
                <span aria-label={`${CHAT_INPUT_MAX_LENGTH}자 중 ${chatInput.length}자 입력`}>{chatInput.length}/{CHAT_INPUT_MAX_LENGTH}</span>
              </div>
              {chatError && <span className="home-chat__error" role="alert">{chatError}</span>}
            </form>
          </section>
        </main>
      {isModalOpen && (
        <div
          ref={modalRef}
          className="modal fade show"
          style={{ display: 'block' }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-chat-modal-title"
          tabIndex={-1}
        >
          <div className="modal-dialog">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title" id="new-chat-modal-title">새 채팅 만들기</h5>
                <button type="button" className="btn-close" aria-label="새 채팅 창 닫기" onClick={() => handleModalClose()} />
              </div>
              <form onSubmit={handleCreateChat}>
                <div className="modal-body">
                  {createChatError && <div className="alert alert-danger" role="alert">{createChatError}</div>}
                  <div className="mb-3">
                    <label className="form-label" htmlFor="department-select">
                      학과 선택
                    </label>
                    <select
                      ref={departmentSelectRef}
                      id="department-select"
                      className="form-select"
                      value={selectedDepartmentId}
                      onChange={(event) => setSelectedDepartmentId(event.target.value)}
                      disabled={departmentsLoading || isCreatingChat}
                    >
                      <option value="">학과를 선택하세요</option>
                      {departments.map((department) => (
                        <option key={department.id} value={department.id}>
                          {department.major?.majorname ?? '학과명 없음'}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="mb-3">
                    <label className="form-label" htmlFor="chat-title-input">
                      채팅방 제목
                    </label>
                    <input
                      id="chat-title-input"
                      type="text"
                      className="form-control"
                      value={chatRoomTitle}
                      onChange={(event) => setChatRoomTitle(event.target.value)}
                      disabled={isCreatingChat}
                      placeholder="예: 장학금 상담"
                    />
                  </div>
                </div>
                <div className="modal-footer">
                  <button type="button" className="btn btn-secondary" onClick={() => handleModalClose()}>
                    닫기
                  </button>
                  <button type="submit" className="btn btn-primary" disabled={isCreatingChat || isNewChatDisabled}>
                    {isCreatingChat ? '생성 중...' : '생성'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
      {isModalOpen && <div className="modal-backdrop fade show" />}
    </div>
  )
}

export default HomePage
