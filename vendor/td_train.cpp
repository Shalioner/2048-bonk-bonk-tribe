/* Native Bonk adaptation of moporgic/TDL2048, commit
 a99f620aec0d30a75943a4c9646743f1f53b0197, 2048.cpp
 optimize:forward 2037-2057, state::instruct 1771-1776.
 The sequential afterstate TD(0) control/update is retained; merge-score reward
 becomes zero, terminal goal/loss becomes 1/0, Bonk engine supplies moves/spawn.
MIT License

Copyright (c) 2021 Hung Guei

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

*/
#include "td_model.h"
#include "engine_adapted.cpp"
#include <mutex>
#include <memory>
#include <exception>

namespace {
std::mutex trainer_mutex;
std::once_flag table_once;
struct Trainer {
    bonk_td::Model model;
    float table[240];
    std::string error;
    Trainer() {
        model.reset();
        for (int s=0;s<16;++s) for(int r=0;r<15;++r) table[s*15+r] = r==0 ? .9f : r==1 ? .1f : 0;
    }
};
struct Episode {
    uint64_t seed, board, moves, caps, updates, attempted_updates, trajectory_digest, first_one, first_two;
    uint32_t has_first_one, has_first_two, result; // 0 loss, 1 goal, 2 limit
};
struct RNG {
    uint64_t state;
    explicit RNG(uint64_t seed) : state(seed) {}
    uint64_t next() {
        uint64_t z = (state += UINT64_C(0x9e3779b97f4a7c15));
        z = (z ^ (z >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)) * UINT64_C(0x94d049bb133111eb);
        return z ^ (z >> 31);
    }
    uint64_t bounded(uint64_t n) {
        const uint64_t threshold = (UINT64_C(0)-n)%n;
        uint64_t value; do { value=next(); } while (value<threshold);
        return value%n;
    }
    double unit() { return static_cast<double>(next()>>11) * (1.0/9007199254740992.0); }
};
void digest(Episode &e, unsigned char tag, uint64_t value) {
    e.trajectory_digest ^= tag; e.trajectory_digest *= UINT64_C(1099511628211);
    for (int byte=0;byte<8;++byte) {
        e.trajectory_digest ^= (value>>(8*byte))&255;
        e.trajectory_digest *= UINT64_C(1099511628211);
    }
}
int legal_mask(board_t b) {
    int mask=0; for(int d=0;d<4;++d) if(execute_move(d,b)!=b) mask|=1<<d;
    return mask;
}
void snapshot(Episode &e, board_t b) {
    const int caps=count_cap(b);
    if (goal(b) || !legal_mask(b)) return;
    if (caps==1 && !e.has_first_one) { e.first_one=b; e.has_first_one=1; }
    if (caps==2 && !e.has_first_two) { e.first_two=b; e.has_first_two=1; }
}
board_t spawn(board_t b, RNG &rng) {
    unsigned empty[16], count=0;
    for(unsigned i=0;i<16;++i) if(((b>>(4*i))&15)==0) empty[count++]=i;
    if(!count) throw std::runtime_error("spawn on full board");
    const unsigned cell=empty[rng.bounded(count)];
    const float *row=spawn_table[get_max_rank(b)];
    // Core set_spawn_table is used for teacher search and stochastic sampling.
    double total=0; for(int rank=0;rank<15;++rank) total+=row[rank];
    const double draw=rng.unit()*total;
    double cumulative=0; unsigned selected=1;
    for(unsigned rank=0;rank<15;++rank) {
        if(row[rank]>0) selected=rank+1;
        cumulative+=row[rank];
        if(draw<cumulative) { selected=rank+1; break; }
    }
    return b | (static_cast<uint64_t>(selected)<<(4*cell));
}
bool probability(float x) { return std::isfinite(x) && x>=0 && x<=1; }
int fail(Trainer *t,const char *message) { if(t)t->error=message; return -1; }
}

extern "C" int td_train_api_version() { return 1; }
extern "C" size_t td_train_result_size() { return sizeof(Episode); }
extern "C" size_t td_train_parameter_count() { return bonk_td::Model::parameter_count(); }
extern "C" void *td_train_create() {
    std::lock_guard<std::mutex> guard(trainer_mutex);
    try { std::call_once(table_once,init_tables); return new Trainer; } catch(...) { return NULL; }
}
extern "C" void td_train_destroy(void *handle) {
    std::lock_guard<std::mutex> guard(trainer_mutex); delete static_cast<Trainer*>(handle);
}
extern "C" const char *td_train_error(void *handle) {
    Trainer *t=static_cast<Trainer*>(handle); return t?t->error.c_str():"Invalid trainer handle";
}
extern "C" int td_train_reset(void *handle,float initial) {
    std::lock_guard<std::mutex> guard(trainer_mutex); Trainer *t=static_cast<Trainer*>(handle);
    if(!t || !probability(initial)) return fail(t,"Invalid initial_value");
    try { t->model.reset(initial); t->error.clear(); return 0; } catch(const std::exception &e) {return fail(t,e.what());}
}
extern "C" int td_train_get_parameters(void *handle,float *values,size_t count) {
    std::lock_guard<std::mutex> guard(trainer_mutex); Trainer *t=static_cast<Trainer*>(handle);
    if(!t || !t->model.get_parameters(values,count)) return fail(t,"Invalid output parameter buffer");
    t->error.clear(); return 0;
}
extern "C" int td_train_set_parameters(void *handle,const float *values,size_t count) {
    std::lock_guard<std::mutex> guard(trainer_mutex); Trainer *t=static_cast<Trainer*>(handle);
    if(!t) return -1;
    try {
        if(!t->model.set_parameters(values,count)) return fail(t,"Invalid input parameters");
        t->error.clear(); return 0;
    } catch(const std::exception &e) {return fail(t,e.what());}
}
extern "C" int td_train_set_spawn_table(void *handle,const float *values,size_t count) {
    std::lock_guard<std::mutex> guard(trainer_mutex); Trainer *t=static_cast<Trainer*>(handle);
    if(!t || !values || count!=240) return fail(t,"Invalid spawn table size");
    float normalized[240];
    for(int s=0;s<16;++s) {
        double total=0;
        for(int r=0;r<15;++r) {float v=values[s*15+r];if(!std::isfinite(v)||v<0)return fail(t,"Invalid spawn table entry");total+=v;}
        if(!(total>0) || !std::isfinite(total)) return fail(t,"Empty spawn row");
        for(int r=0;r<15;++r) normalized[s*15+r]=static_cast<float>(values[s*15+r]/total);
    }
    std::memcpy(t->table,normalized,sizeof(normalized)); t->error.clear(); return 0;
}
extern "C" int td_train_episode(void *handle,uint64_t seed,int algorithm,int behavior,int depth,
        float alpha,float epsilon,int max_moves,int use_start,uint64_t start,Episode *output,size_t size) {
    std::lock_guard<std::mutex> guard(trainer_mutex); Trainer *t=static_cast<Trainer*>(handle);
    if(!t || !output || size!=sizeof(Episode) || algorithm<0 || algorithm>2 || behavior<0 || behavior>1 ||
            depth!=1 || !probability(alpha) || !probability(epsilon) || max_moves<0 || (use_start!=0 && use_start!=1))
        return fail(t,"Invalid episode arguments");
    if(use_start && (!start || goal(start) || !legal_mask(start))) return fail(t,"start_board must be nonterminal player state");
    std::unique_ptr<bonk_td::Model> backup;
    try {
        clear_value_model(); // inference models, never the separate training model
        if(set_spawn_table(t->table,16,15)!=0) return fail(t,"Invalid stored spawn table");
        RNG rng(seed); Episode result={}; result.seed=seed;
        result.trajectory_digest=UINT64_C(14695981039346656037);
        board_t board=use_start?start:spawn(spawn(0,rng),rng);
        digest(result,0,board); snapshot(result,board);
        std::vector<board_t> history;
        board_t previous=0; bool has_previous=false;
        auto instruct = [&](board_t after,float target) {
            if(algorithm==2) return;
            if(!backup) backup.reset(new bonk_td::Model(t->model));
            const float prediction=t->model.update(after,target,alpha);
            if(!std::isfinite(prediction)) throw std::runtime_error("Nonfinite TD update");
            ++result.updates; ++result.attempted_updates;
        };
        while(true) {
            if(goal(board)) { result.result=1; break; }
            const int allowed=legal_mask(board);
            if(!allowed) {result.result=0; break;}
            if(result.moves>=static_cast<uint64_t>(max_moves)) {result.result=2; break;}
            int direction=-1;
            if(behavior==0) direction=solve_levels(board,allowed,1);
            else {
                int choices[4], count=0; float best=-1;
                for(int d=0;d<4;++d) if(allowed&(1<<d)) {
                    choices[count++]=d;
                    const float value=t->model.evaluate(execute_move(d,board));
                    if(value>best) {best=value;direction=d;}
                }
                if(epsilon>0 && rng.unit()<epsilon) direction=choices[rng.bounded(count)];
            }
            if(direction<0 || !(allowed&(1<<direction))) throw std::runtime_error("Behavior selected illegal move");
            const board_t after=execute_move(direction,board);
            // Donor optimize:forward: selected next afterstate is the TD target.
            if(algorithm==0 && has_previous) instruct(previous,t->model.evaluate(after));
            has_previous=false;
            ++result.moves; digest(result,static_cast<unsigned char>(1+direction),after);
            if(goal(after)) {board=after;result.result=1;break;} // no spawn after goal
            if(algorithm==1) history.push_back(after);
            if(algorithm==0) {previous=after;has_previous=true;}
            board=spawn(after,rng); digest(result,5,board); snapshot(result,board);
        }
        if(result.result==2) {
            if(backup) t->model=*backup;
            result.updates=0; // abandoned/censored episodes are not loss labels
        } else {
            const float target=result.result==1?1.0f:0.0f;
            if(algorithm==0 && has_previous) instruct(previous,target);
            if(algorithm==1) for(size_t i=0;i<history.size();++i) instruct(history[i],target);
        }
        result.board=board;result.caps=count_cap(board);digest(result,6,result.result);
        *output=result;t->error.clear();return 0;
    } catch(const std::exception &e) {if(backup)t->model=*backup;return fail(t,e.what());}
    catch(...) {if(backup)t->model=*backup;return fail(t,"Unknown trainer exception");}
}
