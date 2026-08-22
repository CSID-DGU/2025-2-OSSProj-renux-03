namespace RenuxServer.Dtos.EtcDtos;

public class OrganizationDto
{
    public Guid Id { get; init; }
    public MajorDto Major { get; set; } = null!;
    public string? ManagerName { get; set; }

    /// <summary>조직 정보가 마지막으로 바뀐 시각. 관리자 화면의 '최근 갱신' 열에 쓰인다.</summary>
    public DateTime UpdatedTime { get; set; }

    /// <summary>이 학과로 들어와 아직 처리되지 않은 학생회 가입 요청 수.</summary>
    public int PendingRequests { get; set; }
}
