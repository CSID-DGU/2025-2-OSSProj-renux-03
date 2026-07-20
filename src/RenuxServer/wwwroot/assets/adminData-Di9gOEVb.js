const s=e=>{try{const t=JSON.parse(e);return t!==null&&typeof t=="object"&&!Array.isArray(t)?t:{}}catch{return{}}},r=(e,t)=>{const n=e[t];return typeof n=="string"?n:""},a=e=>{const t=s(e.data);return e.source_type==="custom_knowledge"?{title:r(t,"question")||"질문 없음",content:r(t,"answer"),department:r(t,"category")||"공통",requester:r(t,"requester")||"정보 없음"}:e.source_type==="event"?{title:`[행사] ${r(t,"title")}`,content:`일시: ${r(t,"start_date")} ~ ${r(t,"end_date")}
장소: ${r(t,"location")}

${r(t,"description")}`,department:r(t,"department")||"공통",requester:r(t,"requester")||"정보 없음"}:e.source_type==="announcement"?{title:`[공지] ${r(t,"title")}`,content:`게시일: ${r(t,"date")}
분류: ${r(t,"category")}

${r(t,"content")}`,department:r(t,"department")||"공통",requester:r(t,"requester")||"정보 없음"}:{title:"제목 없음",content:"",department:"공통",requester:r(t,"requester")||"정보 없음"}},o=e=>{const t=a(e);return{id:e.id.toString(),departmentName:t.department,submittedAt:e.created_at,handler:t.requester,question:t.title,answer:t.content,status:e.status}},c=e=>{const t=a(e),n=e.status==="approved"||e.status==="approved_manually"?"APPROVED":e.status==="rejected"?"REJECTED":"PENDING";return{id:e.id.toString(),title:t.title,content:t.content,status:n,createdAt:e.created_at}};export{c as a,o as t};
