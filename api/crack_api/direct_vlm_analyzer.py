import os
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional

logger = logging.getLogger("direct_vlm_analyzer")

# KHO TRI THỨC PROMPT CHUYÊN GIA TCVN CHO 14 LỚP HƯ HỎNG (11 CẦU + 3 ĐƯỜNG)
TCVN_PROMPT_KNOWLEDGE = {
    # ================= 11 LỚP CẦU (BRIDGE) =================
    "Crack": {
        "name_vi": "Vết nứt bê tông cầu",
        "tcvn": "TCVN 11823:2017 & TCVN 9345:2012",
        "mechanism": "Ứng suất kéo vượt suất kéo cho phép của bê tông, mỏi do tải trọng xe vượt thiết kế, hoặc co ngót bê tông.",
        "impact": "Tạo đường dẫn cho nước, CO2, ion Chloride xâm nhập ăn mòn cốt thép; làm giảm độ cứng uốn của cấu kiện.",
        "remedy": "Thử nghiệm siêu âm độ sâu vết nứt; bơm keo Epoxy áp lực cao cho vết nứt >= 0.2mm; dán sợi carbon (CFRP) nếu cần gia cường."
    },
    "Efflorescence_Leaching": {
        "name_vi": "Vôi hóa / Rò rỉ khoáng chất",
        "tcvn": "TCVN 9345:2012 (Kết cấu bê tông - Hướng dẫn phòng chống ăn mòn)",
        "mechanism": "Nước đọng thấm qua nứt ngầm hòa tan Ca(OH)2 tự do trong đá bê tông, phản ứng với CO2 không khí tạo kết tủa CaCO3 trắng.",
        "impact": "Báo hiệu hiện tượng thấm nước kéo dài bên trong kết cấu; suy giảm tính kiềm bảo vệ cốt thép của bê tông.",
        "remedy": "Sửa chữa lớp chống thấm bản mặt cầu; tẩy rửa vôi hóa bằng dung dịch axit nhẹ; trám chèn keo chống thấm khe nứt."
    },
    "Exposed Rebar": {
        "name_vi": "Cốt thép lộ thiên",
        "tcvn": "TCVN 11823:2017 & TCVN 9346:2012",
        "mechanism": "Mất lớp bê tông bảo vệ do gỉ nở thanh thép hoặc ăn mòn cacbonat hóa/ion Chloride phá hủy màng thụ động.",
        "impact": "RẤT NGUY HIỂM. Cốt thép bị ăn mòn trực tiếp, suy giảm tiết diện chịu lực, nguy cơ sụp đổ cục bộ hoặc nứt dây chuyền.",
        "remedy": "Đục bỏ bê tông yếu 20mm quanh thép; phun cát SA 2.5 tẩy gỉ; sơn lót Epoxy kẽm ức chế gỉ; trám bù vữa Polyme cường độ cao."
    },
    "Spalling": {
        "name_vi": "Bong tróc / Vỡ ốp bê tông",
        "tcvn": "TCVN 11823:2017 (Cấu kiện bê tông cốt thép dầm cầu)",
        "mechanism": "Áp suất nở của gỉ cốt thép bên trong (Fe2O3 nở 2-6 lần thể tích), tác động cơ học va quẹt hoặc ứng suất nhiệt.",
        "impact": "Suy giảm diện tích chịu nén của bê tông; làm lộ cốt thép chịu lực bên trong.",
        "remedy": "Đục bỏ phần mảng vỡ mục đến bê tông đặc chắc; vệ sinh bám dính; quét liên kết Latex/Epoxy; trám trát vữa Polyme đặc chủng."
    },
    "Staining_Infiltration": {
        "name_vi": "Ố màu / Thấm đọng nước",
        "tcvn": "TCVN 9345:2012",
        "mechanism": "Hệ thống thoát nước mặt cầu bị tắc/hỏng, lớp chống thấm nắp cầu bị thoái hóa hoặc nước rò qua khe co giãn.",
        "impact": "Tạo điều kiện cho quá trình cacbonat hóa và ăn mòn hóa học diễn ra nhanh chóng.",
        "remedy": "Thông rửa ống thoát nước; sửa chữa lớp chống thấm bản nắp cầu; phun phủ dung dịch chống thấm Hydrophobic Silane."
    },
    "Corrosion": {
        "name_vi": "Rỉ sét kim loại & cốt thép",
        "tcvn": "TCVN 9346:2012 (Chống ăn mòn kết cấu bê tông cốt thép)",
        "mechanism": "Phản ứng oxy hóa kim loại khi tiếp xúc với độ ẩm và oxy trong không khí; suy giảm độ pH bê tông dưới 9.0.",
        "impact": "Giảm độ dính kết giữa thép và bê tông; giảm khả năng chịu nén/kéo của cấu kiện.",
        "remedy": "Đánh gỉ kim loại; sơn phủ epoxy bảo vệ; áp dụng bảo vệ catot (Cathodic Protection) trong môi trường mặn."
    },
    "Biological_Growth": {
        "name_vi": "Rêu mốc / Thảm thực vật",
        "tcvn": "Tiêu chuẩn kỹ thuật bảo trì công trình giao thông",
        "mechanism": "Môi trường đọng ẩm lâu ngày, thiếu ánh sáng mặt trời chiếu trực tiếp.",
        "impact": "Giữ ẩm liên tục làm tăng tốc độ phá hủy bê tông; rễ cây nhỏ đâm sâu gây nứt vi mô.",
        "remedy": "Phun hóa chất diệt rêu mốc; cạo rửa bề mặt bằng máy phun nước áp lực cao; cải thiện thoát nước."
    },
    "Pothole Asphalt": {
        "name_vi": "Ổ gà mặt nắp cầu",
        "tcvn": "TCVN 8866:2011 (Mặt đường bê tông nhựa)",
        "mechanism": "Thấm nước đọng làm bong tróc lớp dính bám (Tack coat) kết hợp tải trọng bánh xe nén dập lặp lại.",
        "impact": "Gây xóc nẩy nguy hiểm cho phương tiện giao thông; nước ngấm xuống làm hỏng lớp chống thấm bản nắp cầu.",
        "remedy": "Cắt vuông mép ổ gà; làm sạch tưới nhũ tương dính bám; vá bằng bê tông nhựa nóng tẩm đầm nén chặt."
    },
    "Expansion Joint": {
        "name_vi": "Khuyết tật Khe co giãn",
        "tcvn": "TCVN 11823:2017 (Khe co giãn cầu)",
        "mechanism": "Tải trọng va đập phương tiện; rác thải/đất đá kẹt chèn vào khe; mỏi vật liệu đệm cao su/thép.",
        "impact": "Nước mưa rò rỉ trực tiếp xuống đầu dầm và đá gối cầu gây gỉ sét gối cầu và nứt vỡ vai mố.",
        "remedy": "Nạo vét rác kẹt khe; thay thế đệm cao su khe co giãn; sửa chữa lớp bê tông nẹp khe bằng vữa đặc chủng."
    },
    "Guardrail Damaged": {
        "name_vi": "Hư hỏng Lan can / Rào chắn",
        "tcvn": "TCVN 12681:2019 & QCVN 41:2019/BGTVT",
        "mechanism": "Va chạm cơ học của phương tiện giao thông; ăn mòn môi trường làm bong gỉ sơn.",
        "impact": "Mất khả năng định hướng và ngăn ngừa phương tiện văng khỏi cầu, gây mất an toàn giao thông nghiêm trọng.",
        "remedy": "Nắn chỉnh hoặc thay mới đoạn lan can bị biến dạng; gia cố bu-lông chân lan can; sơn lại phản quang."
    },
    "Control Point": {
        "name_vi": "Điểm khống chế trắc địa",
        "tcvn": "Quy chuẩn đo đạc quan trắc biến dạng công trình",
        "mechanism": "Mốc mạ đồng/thép inox gắn trên bề mặt kết cấu.",
        "impact": "BẢN CHẤT LÀ ĐIỂM MỐC THAM CHIẾU QUAN TRẮC, KHÔNG PHẢI HƯ HỎNG KẾT CẤU.",
        "remedy": "Bảo vệ nguyên trạng mốc trắc địa, không sơn đè hoặc phá hủy."
    },

    # ================= 3 LỚP ĐƯỜNG (ROAD) =================
    "nut": {
        "name_vi": "Vết nứt đơn / Nứt dọc / Nứt ngang mặt đường",
        "tcvn": "TCVN 8866:2011 & QĐ 3588/QĐ-BGTVT",
        "mechanism": "Nứt dọc do mối nối thi công kém hoặc lún lệch lề đường; nứt ngang do co ngót nhiệt khi nhiệt độ thay đổi đột ngột.",
        "impact": "Nước mưa ngấm xuống làm suy yếu lớp móng cấp phối đá dăm bên dưới.",
        "remedy": "Rót nhựa đường nóng hoặc keo trám khe nứt chuyên dụng (Crack Sealant) cho nứt < 5mm."
    },
    "nut_ca_sau": {
        "name_vi": "Nứt cá sấu (Crack Alligator)",
        "tcvn": "TCVN 8866:2011 & QĐ 3588/QĐ-BGTVT",
        "mechanism": "Nền móng đường bị suy yếu nghiêm trọng, mất khả năng chịu tải; mặt đường bị mỏi do lưu lượng xe tải nặng vượt thiết kế.",
        "impact": "Hư hỏng kết cấu nặng (Structural Failure). Nếu không xử lý sẽ nhanh chóng biến thành ổ gà bong bật toàn bộ.",
        "remedy": "Cào bóc toàn bộ lớp bê tông nhựa bị nứt; đào gia cố lại lớp móng đá dăm/gia cố xi măng; thảm bê tông nhựa mới."
    },
    "o_ga_bong_bat": {
        "name_vi": "Ổ gà / Bong bật mảng nhựa",
        "tcvn": "TCVN 8866:2011 & QĐ 3588/QĐ-BGTVT",
        "mechanism": "Sự phát triển từ nứt cá sấu bị đọng nước bôi trơn kết hợp với tải trọng động bánh xe dập nát.",
        "impact": "Gây nguy cơ mất an toàn giao thông rất cao cho xe máy/ô tô; gây đọng nước suy thoái nhanh móng đường.",
        "remedy": "Cắt vuông thành sắc cạnh; đào bỏ vật liệu hư hỏng; tưới nhũ tương dính bám; vá nhựa nóng đầm nén chặt K95/K98."
    }
}

class DirectVLMAnalyzer:
    """
    Phân tích ảnh hư hỏng trực tiếp bằng VLM Multimodal (Qwen2-VL / GPT-4o / Ollama VLM)
    Độc lập 100% không phụ thuộc Chatbot RAGFlow hay Middleware DB.
    """
    def __init__(self):
        self.enabled = os.environ.get("DIRECT_VLM_ENABLED", "false").lower() == "true"
        self.backend = os.environ.get("DIRECT_VLM_BACKEND", "openai").lower()
        self.api_key = os.environ.get("DIRECT_VLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        self.base_url = os.environ.get("DIRECT_VLM_BASE_URL", "https://api.openai.com/v1")
        self.model = os.environ.get("DIRECT_VLM_MODEL", "gpt-4o-mini")
        self.timeout = int(os.environ.get("DIRECT_VLM_TIMEOUT", "6"))

    def generate_system_prompt(self) -> str:
        return (
            "Bạn là Kỹ sư Trưởng kiêm Chuyên gia Giám định Công trình Giao thông Đường bộ & Cầu (Hội đồng Kiểm định Nhà nước).\n"
            "Nhiệm vụ của bạn là phân tích trực tiếp hình ảnh hiện trạng kết hợp với nhãn vị trí hư hỏng được phát hiện.\n"
            "Bạn phải đưa ra đánh giá kỹ thuật trung thực, sắc bén, chính xác 100% theo các tiêu chuẩn Việt Nam hiện hành "
            "(TCVN 11823:2017, TCVN 9345:2012, TCVN 9346:2012, TCVN 8866:2011, TT 41/2024/TT-BGTVT).\n\n"
            "BẠN BẮT BUỘC TRẢ VỀ DUY NHẤT 1 OBJECT JSON NGUYÊN BẢN VỚI CÁC KHÓA SAU:\n"
            "{\n"
            '  "observed_object": "Bộ phận hư hỏng (VD: Dầm bê tông cốt thép / Mặt cầu / Lan can / Áo đường)",\n'
            '  "visual_assessment": "Mô tả đặc điểm hình học, vết nứt, bong tróc, ố màu quan sát được",\n'
            '  "possible_causes": ["Nguyên nhân 1 theo cơ chế vật lý/hóa học", "Nguyên nhân 2 do tải trọng/môi trường"],\n'
            '  "structural_impact": "Đánh giá mức độ ảnh hưởng đến khả năng chịu lực và an toàn khai thác",\n'
            '  "tcvn_reference": "Trích dẫn chính xác tên Tiêu chuẩn Việt Nam áp dụng",\n'
            '  "recommendations": ["Giải pháp sửa chữa 1", "Giải pháp bảo trì 2"]\n'
            "}"
        )

    def generate_user_prompt(self, class_name: str, confidence: float, model_type: str, bbox: list = None) -> str:
        knowledge = TCVN_PROMPT_KNOWLEDGE.get(class_name, {})
        vi_name = knowledge.get("name_vi", class_name)
        tcvn = knowledge.get("tcvn", "TCVN 11823:2017 & TCVN 8866:2011")
        mechanism = knowledge.get("mechanism", "Tác động của môi trường và tải trọng xe.")
        impact = knowledge.get("impact", "Ảnh hưởng đến an toàn khai thác công trình.")
        remedy = knowledge.get("remedy", "Sửa chữa theo quy trình bảo trì.")

        prompt = (
            f"Phân tích đối tượng: {vi_name} ({class_name}) trên công trình loại '{model_type.upper()}'.\n"
            f"Độ tin cậy YOLO: {confidence * 100:.1f}%.\n"
            f"Vị trí Bounding Box: {bbox if bbox else 'Toàn khung ảnh'}.\n\n"
            f"--- DỮ LIỆU THAM CHIẾU TIÊU CHUẨN TCVN ---\n"
            f"- Tiêu chuẩn dẫn chiếu: {tcvn}\n"
            f"- Cơ chế hư hỏng: {mechanism}\n"
            f"- Ảnh hưởng kết cấu: {impact}\n"
            f"- Phương án xử lý: {remedy}\n\n"
            "Hãy nhìn vào bức ảnh và kết hợp với dữ liệu TCVN ở trên để xuất JSON đánh giá kỹ thuật hoàn chỉnh."
        )
        return prompt

    def analyze_detection(
        self,
        class_name: str,
        confidence: float,
        model_type: str,
        image_base64: Optional[str] = None,
        bbox: Optional[list] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Gọi trực tiếp VLM API để phân tích hư hỏng.
        Trả về Dict kết quả hoặc None nếu VLM bị hỏng/tắt.
        """
        if not self.enabled:
            return None

        try:
            user_text = self.generate_user_prompt(class_name, confidence, model_type, bbox)
            messages = [
                {"role": "system", "content": self.generate_system_prompt()},
            ]

            if image_base64:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                        }
                    ]
                })
            else:
                messages.append({"role": "user", "content": user_text})

            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
                "response_format": {"type": "json_object"}
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }

            req_url = f"{self.base_url.rstrip('/')}/chat/completions"
            req_data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(req_url, data=req_data, headers=headers, method="POST")

            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status == 200:
                    resp_body = response.read().decode("utf-8")
                    resp_json = json.loads(resp_body)
                    content_str = resp_json["choices"][0]["message"]["content"]
                    parsed_analysis = json.loads(content_str)
                    logger.info(f"✅ Direct VLM analysis successful for {class_name}")
                    return parsed_analysis

        except Exception as e:
            logger.warning(f"⚠️ Direct VLM analysis skipped/failed: {e}")

        return None

# Singleton Instance
direct_vlm_analyzer = DirectVLMAnalyzer()
