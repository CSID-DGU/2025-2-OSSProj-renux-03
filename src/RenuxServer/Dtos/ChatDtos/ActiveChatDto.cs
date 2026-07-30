using RenuxServer.Dtos.EtcDtos;
using System.Text.Json.Serialization;

namespace RenuxServer.Dtos.ChatDtos;

public class ActiveChatDto
{
    public Guid Id { get; init; }

    public OrganizationDto Organization { get; init; } = null!;

    public string Title { get; set; } = null!;

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? GuestToken { get; init; }

    /// <summary>
    /// 마지막 활동 시각(최근 메시지 없으면 생성 시각).
    /// 사이드바의 날짜 그룹과 정렬 기준이다.
    /// </summary>
    public DateTime UpdatedTime { get; set; }

    /// <summary>
    /// 목록에서 대화를 구분할 수 있게 하는 마지막 메시지 한 줄 미리보기.
    /// 제목만으로는 비슷한 대화를 알아보기 어렵다.
    /// </summary>
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? LastMessage { get; set; }
}
