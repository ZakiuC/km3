#pragma once
#include "../controller.hpp"
#include <deque>
#include <map>
#include <optional>
#include <thread>

namespace pmp4010_cpp_test {
// A protocol-level fake: records actual 8-byte SDO frames, decodes requests,
// and produces wire responses. It never opens a socket or a hardware interface.
class MockCanBus final : public nimotion::CanBus {
public:
    struct Object { std::uint32_t value; unsigned width; };
    explicit MockCanBus(unsigned node = 1) : node_(node) {
        SetObject(0x6041,0,0x40,2); SetObject(0x6040,0,0,2);
        SetObject(0x603f,0,0,2); SetObject(0x1001,0,0,1);
        SetObject(0x6064,0,0,4); SetObject(0x606c,0,0,4);
        SetObject(0x6062,0,12345,4); SetObject(0x607a,0,12345,4);
        SetObject(0x6060,0,1,1); SetObject(0x6061,0,1,1);
        SetObject(0x2002,1,0,2); SetObject(0x2006,0x12,1,2);
        SetObject(0x607c,0,0,4); SetObject(0x607d,1,std::uint32_t(-100000000),4);
        SetObject(0x607d,2,100000000,4); SetObject(0x6080,0,3000,4);
        SetObject(0x607f,0,7000000,4); SetObject(0x6081,0,10000,4);
        SetObject(0x6082,0,0,4); SetObject(0x6083,0,100000,4);
        SetObject(0x6084,0,100000,4); SetObject(0x6085,0,100000,4);
        SetObject(0x60c5,0,1000000,4); SetObject(0x60c6,0,1000000,4);
        SetObject(0x605d,0,1,2); SetObject(0x605a,0,2,2);
        SetObject(0x6067,0,5,4); SetObject(0x200e,2,0x03002312,4);
        SetObject(0x608f,1,131072,4); SetObject(0x608f,2,1,4);
        SetObject(0x6091,1,1,4); SetObject(0x6091,2,1,4);
    }
    void SetObject(std::uint16_t index, std::uint8_t sub, std::uint32_t value, unsigned width) {
        objects_[{index,sub}] = {value,width};
    }
    Object GetObject(std::uint16_t index, std::uint8_t sub=0) const { return objects_.at({index,sub}); }
    const std::vector<nimotion::Frame>& Frames() const { return frames_; }
    std::size_t downloads() const { return downloads_; }
    void SetTimeoutAfter(std::optional<std::size_t> count) { timeout_after_ = count; }
    void SetDelay(int ms) { delay_ms_ = ms; }
    void QueueFrame(const nimotion::Frame& frame) { replies_.push_back(frame); }
    bool send_error = false, receive_error = false, bad_width = false;
    bool wrong_readback = false, stuck_ack = false, no_arrival = false, reset_fails = false;
    std::optional<std::uint16_t> abort_index;
    std::function<void(const nimotion::Frame&, MockCanBus&)> on_send;
    void send(const nimotion::Frame& q) override {
        frames_.push_back(q);
        if (send_error) throw std::runtime_error("mock send failed after enqueue");
        if (on_send) on_send(q,*this);
        if (timeout_after_ && frames_.size() > *timeout_after_) return;
        if (q.id != 0x600+node_ || q.size != 8) throw std::runtime_error("unexpected non-SDO mock frame");
        const auto index=std::uint16_t(nimotion::decode(q.data.data()+1,2));
        const auto sub=q.data[3];
        nimotion::Frame r; r.id=0x580+node_; r.size=8;
        r.data=q.data;
        auto it=objects_.find({index,sub});
        if (it==objects_.end() || (abort_index && index==*abort_index)) {
            r.data[0]=0x80; nimotion::encode(r.data.data()+4,0x06020000,4);
        } else if (q.data[0]==0x40) {
            auto width=bad_width ? 1u : it->second.width;
            r.data[0]=std::uint8_t(0x43|((4-width)<<2));
            auto value=it->second.value;
            if (wrong_readback && index==0x607a) ++value;
            nimotion::encode(r.data.data()+4,value,4);
        } else {
            ++downloads_;
            const unsigned width=4-((q.data[0]>>2)&3);
            if ((q.data[0]&0xe3)!=0x23 || width!=it->second.width)
                throw std::runtime_error("mock download width mismatch");
            it->second.value=nimotion::decode(q.data.data()+4,width);
            r.data[0]=0x60;
            if(index==0x6040) Control(std::uint16_t(it->second.value));
        }
        replies_.push_back(r);
    }
    bool receive(nimotion::Frame& f,int timeout_ms) override {
        if(receive_error) throw std::runtime_error("mock receive failed");
        if(replies_.empty()) {
            if(timeout_ms>0) std::this_thread::sleep_for(std::chrono::milliseconds(timeout_ms));
            return false;
        }
        if(delay_ms_ && timeout_ms>0) std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms_));
        f=replies_.front(); replies_.pop_front(); return true;
    }
private:
    void Control(std::uint16_t cw) {
        auto& sw=objects_.at({0x6041,0}).value;
        if(cw==0x80) {
            if(!reset_fails) {sw=0x40; objects_[{0x603f,0}].value=0; objects_[{0x1001,0}].value=0;}
            return;
        }
        if(cw==0) {sw=(sw&8)?8:((sw&0x80)|0x40); objects_[{0x606c,0}].value=0; return;}
        if(cw==2) {sw=(sw&8)?8:7; objects_[{0x606c,0}].value=0; return;}
        auto state=sw&0x6f;
        if((cw&0xf)==6) state=0x21;
        else if((cw&0xf)==7) state=0x23;
        else if((cw&0xf)==0xf) state=0x27;
        sw=(sw&0x80)|state;
        if(!stuck_ack && (cw&0x10)) {
            sw|=0x1000; objects_[{0x6062,0}].value=objects_[{0x607a,0}].value;
            if(!(cw&0x100) && !no_arrival) objects_[{0x6064,0}].value=objects_[{0x607a,0}].value;
        }
        if(!no_arrival) sw|=0x400;
    }
    unsigned node_;
    std::map<std::pair<std::uint16_t,std::uint8_t>,Object> objects_;
    std::deque<nimotion::Frame> replies_;
    std::vector<nimotion::Frame> frames_;
    std::size_t downloads_=0;
    std::optional<std::size_t> timeout_after_;
    int delay_ms_=0;
};
} // namespace pmp4010_cpp_test
