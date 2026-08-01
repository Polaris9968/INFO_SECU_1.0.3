#include <iostream>
#include <thread>
#include <vector>
#include <set>
#include <chrono>

#include <cryptoTools/Common/block.h>
#include <cryptoTools/Crypto/PRNG.h>
#include <coproto/Socket/BufferingSocket.h>
#include <macoro/sync_wait.h>

#include "oprf.h"
#include "okvsbk.h"
#include "galois128.h"

#ifdef HAVE_OPENMP
#include <omp.h>
#endif

using namespace std;
using namespace osuCrypto;
using namespace coproto;
using namespace oprf;
using okvs::Galois128;

int main()
{
    const int64_t n = std::ceil((double)1.22 * (1 << 8));
    const int64_t w = 96;
    const double  e = 1.15;
    // const int64_t m = static_cast<int64_t>(std::ceil(n * e));

    cout << "Testing OPRF for n=" << n << " ..." << endl;

    auto [sock_sender, sock_receiver] = coproto::LocalAsyncSocket::makePair();

    PRNG commPrng(ZeroBlock);
    block commSeed = commPrng.get();

    vector<block> keys(n);
    std::set<block> used;
    for (int i = 0; i < n; ) {
        block k = commPrng.get();
        if (used.insert(k).second) {
            keys[i] = k;
            ++i;
        }
    }

    for (int i = 0; i < n; ++i) {
        keys[i] = block(i, i);
    }

    vector<block> sender_out(n);
    vector<block> receiver_out(n);

    std::thread th_receiver([&]() {
        try {
            OprfRecver recver;
            recver.init(n, w, e, std::make_shared<coproto::Socket>(sock_receiver), commSeed);

            auto t0 = chrono::high_resolution_clock::now();
            recver.run(keys, receiver_out);
            auto t1 = chrono::high_resolution_clock::now();

            macoro::sync_wait(recver.chl->recv(sender_out));
            auto t2 = chrono::high_resolution_clock::now();

            int errors = 0;
            for (int64_t i = 0; i < n; ++i) {
                if (receiver_out[i] != sender_out[i]) {
                    if (++errors <= 5) {
                        cerr << "Mismatch at index " << i << endl;
                    }
                }
            }

            double enc_time = chrono::duration<double>(t1 - t0).count();
            double total_time = chrono::duration<double>(t2 - t0).count();

            if (errors == 0) {
                cout << "[OK] OPRF evaluation correct for n=" << n << endl;
                cout << "Evaluation time: " << enc_time << " s, total: " << total_time << " s" << endl;
            } else {
                cerr << "[FAIL] errors=" << errors << endl;
            }

        } catch (std::exception& ex) {
            cerr << "Receiver exception: " << ex.what() << endl;
        }
    });

    std::thread th_sender([&]() {
        try {
            OprfSender sender;
            sender.init(n, w, e, std::make_shared<coproto::Socket>(sock_sender), commSeed);

            sender.run();

            vector<block> eval_out;
            sender.eval(keys, eval_out);

            macoro::sync_wait(sender.chl->send(eval_out));
            macoro::sync_wait(sender.chl->flush());

        } catch (std::exception& ex) {
            cerr << "Sender exception: " << ex.what() << endl;
        }
    });

    th_sender.join();
    th_receiver.join();

    cout << "OPRF test finished." << endl;
    return 0;
}
