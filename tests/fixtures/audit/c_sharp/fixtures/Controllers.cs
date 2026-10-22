using Microsoft.AspNetCore.Mvc;
namespace Audit.Web;
[ApiController]
[Route("api/test")]
public class WebController : ControllerBase {
    [HttpGet("ordinary")]
    public string Ordinary() => "ok";
    [Microsoft.AspNetCore.Mvc.HttpPost("qualified")]
    public string Qualified() => "ok";
    [HttpGetAttribute("suffix")]
    public string Suffix() => "ok";
}
