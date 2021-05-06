from keops.python_engine.mapreduce.MapReduce import MapReduce
from keops.python_engine.mapreduce.GpuAssignZero import GpuAssignZero
from keops.python_engine.utils.code_gen_utils import c_variable, c_array, c_include, signature_list, call_list
from keops.python_engine.compilation import Gpu_link_compile


class GpuReduc1D_FromDevice(MapReduce, Gpu_link_compile):
    # class for generating the final C++ code, Gpu version

    AssignZero = GpuAssignZero

    def __init__(self, *args):
        MapReduce.__init__(self, *args)
        Gpu_link_compile.__init__(self)

    def get_code(self):

        super().get_code()

        red_formula = self.red_formula
        dtype = self.dtype
        varloader = self.varloader

        i = c_variable("int", "i")
        j = c_variable("int", "j")
        fout = self.fout
        outi = self.outi
        acc = self.acc
        args = self.args
        sum_scheme = self.sum_scheme

        param_loc = self.param_loc
        xi = self.xi
        yjloc = c_array(dtype, varloader.dimy, f"(yj + threadIdx.x * {varloader.dimy})")
        yjrel = c_array(dtype, varloader.dimy, "yjrel")
        table = varloader.table(self.xi, yjrel, self.param_loc)
        jreltile = c_variable("int", "(jrel + tile * blockDim.x)")

        if dtype == "half2":
            self.headers += c_include("cuda_fp16.h")

        self.code = f"""
                        {self.headers}

                        __global__ void GpuConv1DOnDevice(int nx, int ny, {dtype} *out, {signature_list(args)}) {{
    
                          // get the index of the current thread
                          int i = blockIdx.x * blockDim.x + threadIdx.x;

                          // declare shared mem
                          extern __shared__ {dtype} yj[];

                          // load parameters variables from global memory to local thread memory
                          {param_loc.declare()}
                          {varloader.load_vars("p", param_loc, args)}

                          {fout.declare()}
                          {xi.declare()}
                          {acc.declare()}
                          {sum_scheme.declare_temporary_accumulator()}

                          if (i < nx) {{
                            {red_formula.InitializeReduction(acc)} // acc = 0
                            {sum_scheme.initialize_temporary_accumulator_first_init()}
                            {varloader.load_vars('i', xi, args, row_index=i)} // load xi variables from global memory to local thread memory
                          }}

                          for (int jstart = 0, tile = 0; jstart < ny; jstart += blockDim.x, tile++) {{

                            // get the current column
                            int j = tile * blockDim.x + threadIdx.x;

                            if (j < ny) {{ // we load yj from device global memory only if j<ny
                              {varloader.load_vars("j", yjloc, args, row_index=j)} 
                            }}
                            __syncthreads();

                            if (i < nx) {{ // we compute x1i only if needed
                              {dtype} * yjrel = yj;
                              {sum_scheme.initialize_temporary_accumulator_block_init()}
                              for (int jrel = 0; (jrel < blockDim.x) && (jrel < ny - jstart); jrel++, yjrel += {varloader.dimy}) {{
                                {red_formula.formula(fout,table)} // Call the function, which outputs results in fout
                                {sum_scheme.accumulate_result(acc, fout, jreltile)}
                              }}
                              {sum_scheme.final_operation(acc)}
                            }}
                            __syncthreads();
                          }}
                          if (i < nx) {{
                            {red_formula.FinalizeOutput(acc, outi, i)} 
                          }}

                        }}



                        extern "C" __host__ int launch_keops(int nx, int ny, int device_id, int *ranges, {dtype} *out, {signature_list(args)}) {{

                            // device_id is provided, so we set the GPU device accordingly
                            // Warning : is has to be consistent with location of data
                            cudaSetDevice(device_id);

                            // Compute on device : grid and block are both 1d

                            //SetGpuProps(device_id);

                            dim3 blockSize;

                            blockSize.x = 32;

                            dim3 gridSize;
                            gridSize.x = nx / blockSize.x + (nx % blockSize.x == 0 ? 0 : 1);

                            GpuConv1DOnDevice <<< gridSize, blockSize, blockSize.x * {varloader.dimy} * sizeof({dtype}) >>> (nx, ny, out, {call_list(args)});
    
                            // block until the device has completed
                            cudaDeviceSynchronize();

                            //CudaCheckError();

                            return 0;
                        }}
                    """